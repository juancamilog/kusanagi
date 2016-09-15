import numpy as np 
import utils
from ghost.regression import GP
from ghost.learners.EpisodicLearner import EpisodicLearner
from ghost.learners.PILCO import PILCO
import theano

class TrajectoryMatching(PILCO):
    def __init__(self, params, plant_class, policy_class, cost_func=None, viz_class=None, dynmodel_class=GP.GP_UI, invdynmodel_class=GP.GP_UI, experience = None, async_plant=False, name='TrajectoryMatching', wrap_angles=False, filename_prefix=None):
        self.angle_idims = params['angle_dims']
        self.maxU = params['policy']['maxU']
        # initialize source policy
        params['policy']['source_policy'] = params['source_policy']
        # initialize source policy
        self.source_experience = params['source_experience']
        
        # initialize dynamics model
        # input dimensions to the dynamics model are (state dims - angle dims) + 2*(angle dims) + control dims
        x0 = np.array(params['x0'],dtype='float64').squeeze()
        S0 = np.array(params['S0'],dtype='float64').squeeze()
        self.mx0 = theano.shared(x0.astype('float64'))
        self.Sx0 = theano.shared(S0.astype('float64'))
        dyn_idims = len(x0) + len(self.angle_idims) + len(self.maxU)
        # output dimensions are state dims
        dyn_odims = len(x0)
        # initialize dynamics model (TODO pass this as argument to constructor)
        if 'inv_dynmodel' not in params:
            params['invdynmodel'] = {}
        params['invdynmodel']['idims'] = 2*dyn_odims
        params['invdynmodel']['odims'] = len(self.maxU)#dyn_odims

        self.inverse_dynamics_model = invdynmodel_class(**params['invdynmodel'])
        self.next_episode_inv = 0
        super(TrajectoryMatching, self).__init__(params, plant_class, policy_class, cost_func,viz_class, dynmodel_class,  experience, async_plant, name, filename_prefix)
    
    def set_source_domain(self):
        # TODO Set the source dynamics
        pass

    def set_target_domain(self):
        # TODO Set the source dynamics
        pass

    def train_inverse_dynamics(self):
        utils.print_with_stamp('Training inverse dynamics model',self.name)

        X = []
        Y = []
        n_episodes = len(self.experience.states)
        
        if n_episodes>0:
            # construct training dataset
            for i in xrange(self.next_episode_inv,n_episodes):
                x = np.array(self.experience.states[i])
                u = np.array(self.experience.actions[i])

                # inputs are pairs of consecutive states < x_{t}, x_{t+1} >
                x_ = utils.gTrig_np(x, self.angle_idims)
                #X.append( np.hstack((x_[:-1],x[1:])) )
                X.append( np.hstack((x_[:-1],x_[1:])) )
                # outputs are the actions that produced the input state transition
                Y.append( u[:-1] )

            self.next_episode_inv = n_episodes 
            X = np.vstack(X)
            Y = np.vstack(Y)
            
            # get distribution of initial states
            x0 = np.array([x[0] for x in self.experience.states])
            if n_episodes > 1:
                self.mx0.set_value(x0.mean(0).astype('float64'))
                self.Sx0.set_value(np.cov(x0.T).astype('float64'))
            else:
                self.mx0.set_value(x0.astype('float64').flatten())
                self.Sx0.set_value(1e-2*np.eye(x0.size).astype('float64'))

            # append data to the dynamics model
            self.inverse_dynamics_model.append_dataset(X,Y)
        else:
            x0 = np.array(self.plant.x0, dtype='float64').squeeze()
            S0 = np.array(self.plant.S0, dtype='float64').squeeze()
            self.mx0.set_value(x0)
            self.Sx0.set_value(S0)

        utils.print_with_stamp('Dataset size:: Inputs: [ %s ], Targets: [ %s ]  '%(self.inverse_dynamics_model.X.get_value().shape,self.inverse_dynamics_model.Y.get_value().shape),self.name)
        if self.inverse_dynamics_model.should_recompile:
            # reinitialize log likelihood
            self.inverse_dynamics_model.init_loss()
 
        self.inverse_dynamics_model.train()
        utils.print_with_stamp('Done training inverse dynamics model',self.name)

    def train_adjustment(self):
        n_source_traj=5
        n_target_traj=2
        total_trajectories=len(self.source_experience.states)
        X = []
        Y = []
        Y_var = []
        
        # from sourcce trajectories
        for i in xrange(max(0,total_trajectories-n_source_traj),total_trajectories):
            # for every state transition in the source experience, use the target inverse dynamics
            # to find an action that would produce the desired transition
            x = np.array(self.source_experience.states[i])
            x_s = utils.gTrig_np(x, self.angle_idims)
            # source transitions
            t_s = np.hstack((x_s[:-1],x_s[1:])) 
            # source actions
            u_s = np.array(self.source_experience.actions[i])
            # target actions
            u_t = []
            Su_t = []
            for t_s_i in t_s:
                # get prediction from inverse dynamics model
                u, Su, Cu = self.inverse_dynamics_model.predict(t_s_i)
                u_t.append(np.random.multivariate_normal(u,Su))
                #u_t.append(u)
                Su_t.append(np.diag(np.maximum(Su,1e-9)))

            u_t = np.stack(u_t)
            Su_t = np.stack(Su_t)
            
            if self.policy.use_control_input:
                X.append( np.hstack( [x_s[:-1] , u_s[:-1]] ))
            else:
                X.append( x_s[:-1] )

            Y.append( u_t-u_s[:-1] )
            Y_var.append( Su_t )
        '''
        # from latest OPTIMIZED target trajectories (so it doesn't destroy work done by the trajectory optimizer)
        total_target_trajectories = len(self.experience.states)
        for i in xrange(total_target_trajectories-n_target_traj,total_target_trajectories):
            x_t = np.array(self.experience.states[i])[:-1]
            x_t = utils.gTrig_np(x_t, self.angle_idims)
            u_t = np.array(self.experience.actions[i])[:-1]
            # for every state in the target domain trajectory
            u_s = []
            Su_s = []
            for x in x_t:
                # get the action from the source policy
                u,Su,Cu = self.policy.source_policy.evaluate(x)
                u_s.append(u)
                Su_s.append(1.0*np.ones((u.size,))) # this will put a high weight on the target trajectories

            u_s = np.stack(u_s)
            Su_s = np.stack(Su_s)
            
            if self.policy.use_control_input:
                X.append( np.hstack( [x_t , u_s] ))
            else:
                X.append( x_t )

            Y.append( u_t-u_s )
            Y_var.append( Su_s )
        '''

        X = np.vstack(X)
        Y = np.vstack(Y)
        Y_var = np.vstack(Y_var)

        self.policy.adjustment_model.set_dataset(X,Y,Y_var=Y_var)
        self.policy.adjustment_model.train()

    def sample_trajectory_source(self, N=1):
        # TODO gather data using the source dynamics model or plant
        pass

    def sample_trajectory_target(self, N=1):
        # TODO gather data in the target domain
        pass


