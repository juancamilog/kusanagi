import numpy as np
import theano
import theano.tensor as tt

from kusanagi import utils
from kusanagi.ghost.regression import RBFGP, SSGP_UI,GP, BNN
from kusanagi.ghost.regression import cov
from kusanagi.ghost.control.saturation import gSat
from kusanagi.utils import gTrig2, gTrig2_np
from kusanagi.base.Loadable import Loadable
from functools import partial
from scipy.optimize import minimize
from scipy.cluster.vq import kmeans2,vq

# GP based controller
class RBFPolicy(RBFGP):
    def __init__(self, idims=None, odims=None, sat_func=gSat,  m0=None, S0=None, maxU=[10], n_inducing=10, angle_dims=[], name='RBFPolicy', filename=None, max_evals=750 ,*kwargs):
        self.maxU = np.array(maxU)
        self.n_inducing = n_inducing
        self.angle_dims = angle_dims
        self.name = name

        if sat_func:
            # set the model to be a RBF with saturated outputs
            sat_func = partial(sat_func, e=maxU)

        if filename is not None:
            # try loading from file
            super(RBFPolicy, self).__init__(idims=0, odims=0, sat_func=sat_func, max_evals=max_evals, name=self.name, filename=filename)
            #self.load()
        else:
            self.m0 = np.array(m0,dtype=theano.config.floatX)
            self.S0 = np.array(S0,dtype=theano.config.floatX)
            
            if not idims:
                idims = len(self.m0) + len(self.angle_dims)
            if not odims:
                odims = len(self.maxU)
            super(RBFPolicy, self).__init__(idims=idims, odims=odims, sat_func=sat_func, max_evals=max_evals, name=self.name)
            self.init_params()
        
        # make sure we always get the parameters in the same order
        self.param_names = ['X','Y','loghyp_full']

    def load(self, output_folder=None,output_filename=None):
        ''' loads the state from file, and initializes additional variables'''
        # load state
        super(RBFGP,self).load(output_folder,output_filename)
        
        # initialize mising variables
        self.loghyp = tt.concatenate([self.loghyp_full[:,:-2], theano.gradient.disconnected_grad(self.loghyp_full[:,-2:])], axis=np.array(1,dtype='int64'))

        # loghyp is no longer the trainable paramter
        if 'loghyp' in self.param_names: self.param_names.remove('loghyp')

    def init_params(self,compile_funcs=False):
        utils.print_with_stamp('Initializing parameters',self.name)
        # initialize the mean and covariance of the inputs
        m0,S0 = self.m0,self.S0
        if len(self.angle_dims)>0:
            m0, S0 = utils.gTrig2_np(np.array(m0)[None,:], np.array(S0)[None,:,:], self.angle_dims, len(m0))
            m0 = m0.squeeze(); S0 = S0.squeeze();
        # init inputs
        L_noise = np.linalg.cholesky(S0)
        inputs = np.array([m0 + np.random.randn(S0.shape[1]).dot(L_noise) for i in xrange(self.n_inducing)]);

        # set the initial log hyperparameters (1 for linear dimensions, 0.7 for angular)
        l0 = np.hstack([np.ones(self.m0.size-len(self.angle_dims)),0.7*np.ones(2*len(self.angle_dims)),1,0.01])
        l0 = np.log(np.tile(l0,(self.maxU.size,1)))

        # init policy targets close to zero
        targets = 0.1*np.random.randn(self.n_inducing,self.maxU.size)
        
        self.trained = False

        # set the parameters
        self.N = inputs.shape[0]
        self.D = inputs.shape[1]
        self.E = targets.shape[1]
        
        self.set_params( {'X': inputs.astype(theano.config.floatX), 'Y': targets.astype(theano.config.floatX)} )
        self.set_params( {'loghyp_full': l0.astype(theano.config.floatX)} )
        
        # don't optimize the signal and noise variances
        self.loghyp = tt.concatenate([self.loghyp_full[:,:-2], theano.gradient.disconnected_grad(self.loghyp_full[:,-2:])], axis=np.array(1,dtype='int64'))

        # loghyp is no longer the trainable paramter
        if 'loghyp' in self.param_names: self.param_names.remove('loghyp')
        
        # call init loss to initialize the intermediate shared variables
        super(RBFGP,self).init_loss(cache_vars=False,compile_funcs=compile_funcs)
        # init the prediction function 
        self.evaluate(np.zeros((self.D,)))

    def evaluate(self, m, s=None, t=None, symbolic=False):
        D = m.shape[0]
        if symbolic:
            ret = self.predict_symbolic(m,s)
        else:
            ret = self.predict(m,s)
        return ret 

# random controller
class RandPolicy:
    def __init__(self, maxU=[10], random_walk=False):
        self.maxU = np.array(maxU)
        #self.last_u = np.zeros_like(np.array(maxU))
        self.random_walk=random_walk

    def evaluate(self, m, s=None, t=None, symbolic=False):
        if self.random_walk:
            new_u = ((2*np.random.random(self.maxU.size)-1.0)).reshape(self.maxU.shape)*self.maxU
            ret = self.last_u + 0.2*new_u if t is None or t != 0 else new_u
            ret = np.min ( (ret.flatten(), self.maxU.flatten()), axis=0  ) 
            ret = np.max ( (ret.flatten(), -self.maxU.flatten()), axis=0  ) 
            ret = ret.reshape(self.maxU.shape)
        else:
            ret = ((2*np.random.random(self.maxU.size)-1.0)).reshape(self.maxU.shape)*self.maxU

        self.last_u = ret
        U = len(self.maxU)
        D = m.shape[0]
        return ret, np.zeros((U,U)), np.zeros((D,U))

# linear time varying policy
class LocalLinearPolicy(Loadable):
    def __init__(self, H, dt, m0, S0=None, maxU=[10], angle_dims=[], name='LocalLinearPolicy', **kwargs):
        self.maxU = np.array(maxU)
        self.angle_dims = angle_dims
        self.H = H
        self.dt = dt
        self.m0 = m0
        D = len(self.m0)
        self.S0 = S0 if S0 is not None else np.zeros((D,D))
        self.t = 0
        self.noise = 0
        self.name = name
        self.init_params()

        Loadable.__init__(self,name=name,filename=self.filename)
        # register theano functions and shared variables for saving
        self.register_types([tt.sharedvar.SharedVariable, theano.compile.function_module.Function])

    def init_params(self):
        H_steps = int(np.ceil(self.H/self.dt))
        self.state_changed = False

        # set random (uniform distribution) controls
        u = self.maxU*(2*np.random.random((H_steps,len(self.maxU))) - 1)
        self.u_nominal = theano.shared(u)

        # intialize the nominal states to the appropriate size
        m0, S0 = utils.gTrig2_np(np.array(self.m0)[None,:], np.array(self.S0)[None,:,:], self.angle_dims, len(self.m0))
        self.triu_indices = np.triu_indices(m0.size)
        z0 = np.concatenate([m0.flatten(),S0[0][self.triu_indices]])
        z = np.tile(z0,(H_steps,1))
        self.z_nominal = theano.shared(z)

        # initialize the open loop and feedback matrices 
        I = np.zeros( (H_steps, len(self.maxU)) )
        L = np.zeros( (H_steps, len(self.maxU), z0.size) )
        self.I = theano.shared(I)
        self.L = theano.shared(L)
        
        # set a meaningful filename
        self.filename = self.name+'_'+str(len(self.m0))+'_'+str(len(self.maxU))

    def evaluate(self, m, s=None, t=None, symbolic=False):
        D = m.shape[0]
        if t is not None:
            self.t = t
        t = self.t

        u,z,I,L = self.u_nominal,self.z_nominal,self.I,self.L

        if symbolic:
            tt = theano.tensor
        else:
            tt = np
            u,z,I,L=u.get_value(),z.get_value(),I.get_value(),L.get_value()
            
        if s is None:
            s = tt.zeros((D,D))
        
        # construct flattened state covariance vector
        z_t = tt.concatenate([m.flatten(),s[self.triu_indices]])
        # compute control
        u_t = u[t] + I[t] + L[t].dot(z_t - z[t])
        # add random noise if requested (only for non symbolic)
        if not symbolic and self.noise and self.noise > 0:
            u_t += self.noise*tt.random.randn(*u_t.shape)

        # limit the controller output
        #u_t = tt.maximum(u_t, -self.maxU)
        #u_t = tt.minimum(u_t, self.maxU)

        U = u_t.shape[0]
        self.t+=1
        return u_t, tt.zeros((U,U)), tt.zeros((D,U))

    def get_params(self, symbolic=False, t=None):
        params = [self.u_nominal,self.z_nominal,self.I,self.L]

        if not symbolic:
            params = [ p.get_value() for p in params]
        return params

    def get_all_shared_vars(self):
        return [attr for attr in self.__dict__.values() if isinstance(attr,tt.sharedvar.SharedVariable)]

class AdjustedPolicy:
    def __init__(self, source_policy, maxU=[10], angle_dims=[], name='AdjustedPolicy', adjustment_model_class=SSGP_UI, use_control_input=True, **kwargs):
        self.use_control_input = use_control_input
        self.angle_dims = angle_dims
        self.name = name
        self.maxU=maxU
        
        self.source_policy = source_policy
        #self.source_policy.init_loss(cache_vars=False)
        #self.source_policy.init_predict()
        self.adjustment_model = adjustment_model_class(idims=self.source_policy.D, odims=self.source_policy.E, name='AdjustmentModel',**kwargs) #TODO we may add a saturating function here
    
    def init_params(self):
        #self.source_policy.init_params() TODO
        pass

    def evaluate(self, m, S=None, t=None, symbolic=False):
        tt = theano.tensor if symbolic else np
        # get the output of the source policy
        mu,Su,Cu = self.source_policy.evaluate(m,S,t,symbolic)

        if self.adjustment_model.trained == True:
            # initialize the inputs to the policy adjustment function
            adj_input_m = m
            adj_input_S = S if S is not None else tt.zeros((m.size,m.size))

            if self.use_control_input:
                adj_input_m = tt.concatenate([adj_input_m,mu])
                # fill input convariance matrix
                q = adj_input_S.dot(Cu)
                Sxu_up = tt.concatenate([adj_input_S,q],axis=1)
                Sxu_lo = tt.concatenate([q.T,Su],axis=1)
                adj_input_S = tt.concatenate([Sxu_up,Sxu_lo],axis=0) # [D+U]x[D+U]

            if symbolic:
                madj,Sadj,Cadj = self.adjustment_model.predict_symbolic(adj_input_m,adj_input_S)
            else:
                madj,Sadj,Cadj = self.adjustment_model.predict(adj_input_m,adj_input_S)

            # compute the adjusted control distribution
            mu = mu + madj
            Sxu_adj = adj_input_S.dot(Cadj)
            Su_adj = Sxu_adj[m.size:]
            Su = Su + Sadj + Su_adj + Su_adj.T
            if S is not None:
                if symbolic:
                    Cu = Cu + tt.nlinalg.matrix_inverse(S).dot(Sxu_adj[:m.size])
                else:
                    Cu = Cu + np.linalg.pinv(S).dot(Sxu_adj[:m.size])

        return mu,Su,Cu

    def get_params(self, symbolic=False):
        return self.adjustment_model.get_params(symbolic)

    def set_params(self,params):
        return self.adjustment_model.set_params(params)

    def get_all_shared_vars(self):
        return self.source_policy.get_all_shared_vars()+self.adjustment_model.get_all_shared_vars()

    def load(self, output_folder=None,output_filename=None):
        self.adjustment_model.load(output_folder,output_filename)

    def save(self, output_folder=None,output_filename=None):
        self.adjustment_model.save(output_folder,output_filename)

# GP based controller
class BNNPolicy(BNN):
    def __init__(self, m0=None, S0=None, maxU=[10], hidden_dims=[20,20,20], angle_dims=[], name='NNPolicy', filename=None):
        self.maxU = np.array(maxU)
        self.angle_dims = angle_dims
        self.name = name
        # set the model to be a RBF with saturated outputs
        sat_func = partial(gSat, e=maxU)

        if filename is not None:
            # try loading from file
            self.uncertain_inputs = True
            self.filename = filename
            self.sat_func = sat_func
            self.load()
        else:
            self.m0 = np.array(m0)
            self.S0 = np.array(S0)

            policy_idims = len(self.m0) + len(self.angle_dims)
            policy_odims = len(self.maxU)
            super(NNPolicy, self).__init__(idims=policy_idims, hidden_dims=hidden_dims, odims=policy_odims, sat_func=sat_func, name=self.name)
            
            # check if we need to initialize
            params = self.get_params()
            for p in params:
                if p is None or p.size == 0:
                    self.init_params()
                    break

    def init_params(self):
        self.init_loss()
        self.init_predict()

    def evaluate(self, m, s=None, t=None, derivs=False, symbolic=False):
        D = m.shape[0]
        if symbolic:
            if s is None:
                s = tt.zeros((D,D))
            ret = self.predict_symbolic(m,s)
        else:
            if s is None:
                s = np.zeros((D,D))
            ret = self.predict(m,s) if not derivs else self.predict_d(m,s)
        return ret 
