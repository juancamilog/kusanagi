import numpy as np
from utils import print_with_stamp,gTrig_np,gTrig2
from ghost.learners.EpisodicLearner import *
from ghost.regression.GPRegressor import GP_UI
import theano
from theano.misc.pkl_utils import dump as t_dump, load as t_load

class PILCO(EpisodicLearner):
    def __init__(self, plant, policy, cost, angle_idims=None, discount=1, experience = None, async_plant=True, name='PILCO', wrap_angles=False):
        super(PILCO, self).__init__(plant, policy, cost, angle_idims, discount, experience, async_plant, name)
        self.dynamics_model = None
        self.wrap_angles = wrap_angles
        self.rollout=None
        self.policy_gradients=None

    def init_rollout(self, derivs=False):
        ''' This compiles the rollout function, which applies the policy and predicts the next state 
            of the system using the learn GP dynamics model '''
        
        # define the function for a single propagation step
        def rollout_single_step(mx,Sx):
            D=mx.shape[0]

            # convert angles from input distribution to its complex representation
            mxa,Sxa,Ca = gTrig2(mx,Sx,self.angle_idims,self.mx0.size)

            # compute distribution of control signal
            logsn = self.dynamics_model.loghyp[:,-1]
            Sx_ = Sx + theano.tensor.diag(0.5*theano.tensor.exp(2*logsn))# noisy state measurement
            mxa_,Sxa_,Ca = gTrig2(mx,Sx_,self.angle_idims,self.mx0.size)
            mu, Su, Cu = self.policy.model.predict_symbolic(mxa_, Sxa_)
            
            # compute state control joint distribution
            n = Sxa.shape[0]; Da = Sxa.shape[1]; U = Su.shape[1]
            idimsa = Da + U
            mxu = theano.tensor.concatenate([mxa,mu])
            Sxu = theano.tensor.zeros((idimsa,idimsa))
            Sxu = theano.tensor.set_subtensor(Sxu[:Da,:Da], Sxa)
            Sxu = theano.tensor.set_subtensor(Sxu[Da:,Da:], Su)
            q = Sxa.dot(Cu)
            Sxu = theano.tensor.set_subtensor(Sxu[:Da,Da:], q )
            Sxu = theano.tensor.set_subtensor(Sxu[Da:,:Da], q.T )

            # state control covariance without angle dimensions
            na_dims = list(set(range(self.mx0.size)).difference(self.angle_idims))
            Dna = len(na_dims)
            Sx_xa = theano.tensor.zeros((D,Da))
            Sx_xa = theano.tensor.set_subtensor(Sx_xa[:D,:Dna], Sx[:,na_dims])
            Sx_xa = theano.tensor.set_subtensor(Sx_xa[:D,Dna:], Sx.dot(Ca))
            Sxu_ = theano.tensor.zeros((D,Da+U))
            Sxu_ = theano.tensor.set_subtensor(Sxu_[:D,:Da], Sx_xa)
            Sxu_ = theano.tensor.set_subtensor(Sxu_[:D,Da:], Sx_xa.dot(Cu))

            #  predict the change in state given current state-action
            # C_deltax = inv (Sxu) dot Sxu_deltax
            m_deltax, S_deltax, C_deltax = self.dynamics_model.predict_symbolic(mxu,Sxu)

            # compute the successor state distribution
            mx_next = mx + m_deltax
            Sx_deltax = Sxu_.dot(C_deltax)
            Sx_next = Sx + S_deltax + Sx_deltax + Sx_deltax.T

            #  get cost:
            mcost, Scost = self.cost_symbolic(mx_next,Sx_next)

            return [mcost,Scost,mx_next,Sx_next]

        # define input variables
        print_with_stamp('Computing symbolic expression graph for belief state propagation',self.name)
        mx = theano.tensor.vector('mx')
        Sx = theano.tensor.matrix('Sx')
        H = theano.tensor.iscalar('H')
        gamma = theano.tensor.scalar('gamma')

        # this will generate the list of value and state distributions for each time step
        def get_discounted_reward(mv,Sv,mx,Sx,gamma):
            mv_next, Sv_next, mx_next, Sx_next = rollout_single_step(mx,Sx)
            return gamma*mv_next,(gamma**2)*Sv_next, mx_next, Sx_next, gamma*gamma
        zero_ct = theano.tensor.as_tensor_variable(np.asarray(0,mx.dtype))
        (mV_,SV_,mx_,Sx_,gamma_), updts = theano.scan(fn=get_discounted_reward, outputs_info=[zero_ct,zero_ct,mx,Sx,gamma], n_steps=H)
        
        if derivs :
            print_with_stamp('Computing symbolic expression for policy gradients',self.name)
            dretvars = [mV_.sum()]
            params = self.policy.get_params(symbolic=True)
            if not isinstance(params,list):
                params = [params]
            for p in params:
                dretvars.append( theano.tensor.grad(mV_.sum(), p ) ) # we are only interested in the derivative of the sum of expected values

            print_with_stamp('Compiling belief state propagation',self.name)
            self.rollout = theano.function([mx,Sx,H,gamma], (mV_,SV_,mx_,Sx_), allow_input_downcast=True, updates=updts)
            print_with_stamp('Compiling policy gradients',self.name)
            self.policy_gradients = theano.function([mx,Sx,H,gamma], dretvars, allow_input_downcast=True, updates=updts)
        else:
            print_with_stamp('Compiling belief state propagation',self.name)
            self.rollout = theano.function([mx,Sx,H,gamma], (mV_,SV_,mx_,Sx_), allow_input_downcast=True, updates=updts)
        print_with_stamp('Done compiling.',self.name)

    def train_dynamics(self):
        print_with_stamp('Training dynamics model',self.name)
        
        X = []
        Y = []
        x0 = []
        n_episodes = len(self.experience.states)
        # construct training dataset
        for i in xrange(n_episodes):
            x = np.array(self.experience.states[i])
            u = np.array(self.experience.actions[i])
            x0.append(x[0])

            # inputs are states, concatenated with actions (except for the last entry)
            x_ = gTrig_np(x, self.angle_idims)
            X.append( np.hstack((x_[:-1],u[:-1])) )
            # outputs are changes in state
            Y.append( x[1:] - x[:-1] )

        X = np.vstack(X)
        Y = np.vstack(Y)

        # get distribution of initial states
        x0 = np.array(x0)
        if n_episodes > 1:
            self.mx0 = x0.mean(0)[None,:]
            self.Sx0 = np.cov(x0.T)[None,:,:]
        else:
            self.mx0 = x0[None,:]
            self.Sx0 = 1e-2*np.eye(len(x0))[None,:,:]

        if self.wrap_angles:
            # wrap angle differences to [-pi,pi]
            Y[:,self.angle_idims] = (Y[:,self.angle_idims] + np.pi) % (2 * np.pi ) - np.pi

        if self.dynamics_model is None:
            self.dynamics_model = GP_UI(X,Y)
        else:
            self.dynamics_model.set_dataset(X,Y)
 

        self.dynamics_model.set_loghyp( np.array([[ 5.509997626011774,   5.695797298947197,   5.692015197025450,   5.638201671096178],
                                                  [ 2.172712533907143,   4.666315918776204,   4.612552250383755,   4.942292180454226],
                                                  [ 5.146257432313829,   2.296298649465123,   2.456701520947445,   2.614396832176411],
                                                  [ 2.890900478963427,   0.214158569334003,   0.224852498280747,   0.493471679361221],
                                                  [ 1.797786347384082,   1.586977710450885,  -0.058171149174515,  -0.345601213254841],
                                                  [ 4.069820471633279,   3.013197670171895,   2.934665512611971,   3.616775778040698],
                                                  [-0.984097642284717,   0.137890342258600,   1.299603816102084,  -0.611523812016797],
                                                  [-4.115382429098694,  -4.115897287345494,  -3.168863634366222,  -4.247869488525884]]).T);

        #self.dynamics_model.train()
        self.dynamics_model.save()
        print_with_stamp('Done training dynamics model',self.name)

    def value(self, derivs=False):
        #print_with_stamp('Computing value of current policy',self.name)

        # compile the belef state propagation
        if self.rollout is None or self.policy_gradients is None:
            self.init_rollout(derivs=derivs)
        
        # setp initial state
        mx = np.array(self.plant.x0)
        Sx = np.array(self.plant.S0)
        
        if not derivs:
            # self.H is the number of steps to rollout ( finite horizon )
            ret = self.rollout(mx,Sx,self.H,discount)
            return ret[0].sum()
        else:
            ret = self.policy_gradients(mx,Sx,self.H,discount)
            # first return argument is the value of the policy, second are the gradients wrt the policy params wrapped into single vector 
            return [ret[0],self.wrap_policy_params(ret[1:])]
