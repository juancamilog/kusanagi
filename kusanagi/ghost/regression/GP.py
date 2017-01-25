import os
import numpy as np
import theano
import theano.tensor as tt

from collections import OrderedDict
from functools import partial
from scipy.optimize import minimize, basinhopping
from scipy.cluster.vq import kmeans
from theano import function as F, shared as S
from theano.tensor.nlinalg import matrix_dot
from theano.sandbox.linalg import psd,matrix_inverse,det,cholesky
from theano.tensor.slinalg import solve_lower_triangular, solve_upper_triangular, solve

import cov
import SNRpenalty
from kusanagi import utils
from kusanagi.base.Loadable import Loadable

DETERMINISTIC_MIN_METHODS = ['L-BFGS-B', 'TNC', 'BFGS', 'SLSQP', 'CG']
class GP(Loadable):
    def __init__(self, X_dataset=None, Y_dataset=None, name='GP', idims=None, odims=None, profile=theano.config.profile, uncertain_inputs=False, snr_penalty=SNRpenalty.SEard, filename=None, **kwargs):
        # theano options
        self.profile= profile
        self.compile_mode = theano.compile.get_default_mode()#.excluding('scanOp_pushout_seqs_ops')

        # GP options
        self.max_evals = kwargs['max_evals'] if 'max_evals' in kwargs else 500
        self.conv_thr = kwargs['conv_thr'] if 'conv_thr' in kwargs else 1e-12
        self.min_method = kwargs['min_method'] if 'min_method' in kwargs else 'L-BFGS-B'#utils.fmin_lbfgs
        self.state_changed = True
        self.should_recompile = False
        self.trained = False
        self.uncertain_inputs = uncertain_inputs
        self.snr_penalty = snr_penalty
        self.covs = (cov.SEard, cov.Noise)
        self.fixed_params = []
        
        # dimension related variables
        self.N = 0
        if X_dataset is None:
            if idims is None:
                raise ValueError('You need to either provide X_dataset (n x idims numpy array) or a value for idims') 
            self.D = idims
        else:
            self.D = X_dataset.shape[1]

        if Y_dataset is None:
            if odims is None:
                raise ValueError('You need to either provide Y_dataset (n x odims numpy array) or a value for odims') 
            self.E = odims
        else:
            self.E = Y_dataset.shape[1]

        #symbolic varianbles
        self.param_names = []
        self.loghyp = None; self.logsn = None
        self.X = None; self.Y = None;
        self.iK = None; self.L = None; self.beta = None; self.nigp=None; self.Y_var=None; self.X_cov=None
        self.kernel_func = None
        self.loss_fn = None; self.dloss_fn=None

        # compiled functions
        self.predict_fn = None
        self.predict_d_fn = None
        
        # name of this class for printing command line output and saving
        self.name = name
        # filename for saving
        self.filename = '%s_%d_%d_%s_%s'%(self.name,self.D,self.E,theano.config.device,theano.config.floatX)
        if filename is not None:
            self.filename = filename
            Loadable.__init__(self,name=name,filename=self.filename)
            self.load()
        else:
            Loadable.__init__(self,name=name,filename=self.filename)
        

        # register theanno functions and shared variables for saving
        self.register_types([tt.sharedvar.SharedVariable, theano.compile.function_module.Function])
        # register additional variables for saving
        self.register(['trained', 'param_names', 'fixed_params'])
        
        # initialize the class if no pickled version is available
        if X_dataset is not None and Y_dataset is not None:
            utils.print_with_stamp('Initialising new GP regressor',self.name)
            self.set_dataset(X_dataset,Y_dataset)
            utils.print_with_stamp('Finished initialising GP regressor',self.name)
        
        self.ready = False

    def load(self, output_folder=None,output_filename=None):
        ''' loads the state from file, and initializes additional variables'''
        # load state
        super(GP,self).load(output_folder,output_filename)
        
        # initialize missing variables
        if hasattr(self,'X') and self.X:
            self.N = self.X.get_value(borrow=True).shape[0]
            self.D = self.X.get_value(borrow=True).shape[1]
        if hasattr(self,'Y') and self.Y:
            self.E = self.Y.get_value(borrow=True).shape[1]
        if hasattr(self,'loghyp') and self.loghyp:
            self.logsn = self.loghyp[:,-1]

    def get_dataset(self):
        return self.X.get_value(), self.Y.get_value()

    def set_dataset(self,X_dataset,Y_dataset,X_cov=None,Y_var=None):
        # ensure we don't change the number of input and output dimensions ( the number of samples can change)
        assert X_dataset.shape[0] == Y_dataset.shape[0], "X_dataset and Y_dataset must have the same number of rows"
        if hasattr(self,'X') and self.X:
            assert self.X.get_value(borrow=True).shape[1] == X_dataset.shape[1]
        if hasattr(self,'Y') and self.Y:
            assert self.Y.get_value(borrow=True).shape[1] == Y_dataset.shape[1]
        
        # first, convert numpy arrays to appropriate type
        X_dataset = X_dataset.astype( 'float64' )# + 1e-5*np.random.randn(*X_dataset.shape)
        Y_dataset = Y_dataset.astype( 'float64' )
        # dims = non_angle_dims + 2*angle_dims
        self.N = X_dataset.shape[0]
        self.D = X_dataset.shape[1]
        self.E = Y_dataset.shape[1]

        # now we create symbolic shared variables
        if self.X is None:
            self.X = S(X_dataset,name='%s>X'%(self.name),borrow=True)
        else:
            self.X.set_value(X_dataset,borrow=True)
        if self.Y is None:
            self.Y = S(Y_dataset,name='%s>Y'%(self.name),borrow=True)
        else:
            self.Y.set_value(Y_dataset,borrow=True)
        
        self.X_cov = X_cov
        if Y_var is not None:
            if self.Y_var is None:
                self.Y_var = S(Y_var,name='%s>Y_var'%(self.name),borrow=True)
            else:
                self.Y_var.set_value(Y_var,borrow=True)

        if not self.trained:
            # init log hyperparameters
            self.init_params()

        # we should be saving, since we updated the trianing dataset
        self.state_changed = True
        if (self.N > 0):
            self.ready = True

    def append_dataset(self,X_dataset,Y_dataset,X_cov=None,Y_var=None):
        if self.X is None:
            self.set_dataset(X_dataset,Y_dataset,X_cov,Y_var)
        else:
            X_ = np.vstack((self.X.get_value(), X_dataset.astype(self.X.dtype)))
            Y_ = np.vstack((self.Y.get_value(), Y_dataset.astype(self.Y.dtype)))
            X_cov_ = None
            if X_cov is not None and hasattr(self,'X_cov') and self.X_cov:
                X_cov_ = np.vstack((self.X_cov, X_cov.astype(self.X_cov.dtype)))
            Y_var_ = None
            if Y_var is not None and hasattr(self,'Y_var'):
                Y_var_ = np.vstack((self.Y_var.get_value(), Y_var.astype(self.Y_var.dtype)))
            
            self.set_dataset(X_,Y_,X_cov_,Y_var_)

    def init_params(self):
        utils.print_with_stamp('Initialising parameters' ,self.name)
        idims = self.D; odims = self.E; 
        # initialize the loghyperparameters of the gp ( this code supports squared exponential only, at the moment)
        X = self.X.get_value(); Y = self.Y.get_value()
        loghyp = np.zeros((odims,idims+2))
        loghyp[:,:idims] = 0.5*X.std(0,ddof=1)
        loghyp[:,idims] = 0.5*Y.std(0,ddof=1)
        loghyp[:,idims+1] = 0.1*loghyp[:,idims]
        loghyp = np.log(loghyp)
        
        # set params will either create the loghyp attribute, or update its value
        self.set_params({'loghyp': loghyp})
        # create logsn (used in PILCO)
        if self.logsn is None:
            self.logsn = self.loghyp[:,-1]

    def set_params(self, params):
        if type(params) is list:
            params = dict(zip(self.param_names,params))
        for pname in params.keys():
            # create shared variable if it doesn't exist
            if pname not in self.__dict__ or self.__dict__[pname] is None:
                p = S(params[pname],name='%s>%s'%(self.name,pname),borrow=True)
                self.__dict__[pname] = p
                if pname not in self.param_names:
                    self.param_names.append(pname)
            # otherwise, update the value of the shared variable
            else:
                p = self.__dict__[pname]
                pv = params[pname].reshape(p.get_value(borrow=True).shape)
                p.set_value(pv,borrow=True)

    def get_params(self, symbolic=False, as_dict=False, ignore_fixed=True):
        if ignore_fixed:
            params = [ self.__dict__[pname] for pname in self.param_names if (pname in self.__dict__ and self.__dict__[pname] and not pname in self.fixed_params) ]
        else:
            params = [ self.__dict__[pname] for pname in self.param_names if (pname in self.__dict__ and self.__dict__[pname]) ]

        if not symbolic:
            params = [ p.get_value() for p in params]
        if as_dict:
            params = dict(zip(self.param_names,params))
        return params

    def get_all_shared_vars(self, as_dict=False):
        if as_dict:
            return [(attr_name,self.__dict__[attr_name]) for attr_name in self.__dict__.keys() if isinstance(self.__dict__[attr_name],tt.sharedvar.SharedVariable)]
        else:
            return [attr for attr in self.__dict__.values() if isinstance(attr,tt.sharedvar.SharedVariable)]

    def init_loss(self, cache_vars=True, compile_funcs=True):
        utils.print_with_stamp('Initialising expression graph for full GP training loss function',self.name)
        idims = self.D
        odims = self.E

        # these are shared variables for the kernel matrix, its cholesky decomposition and K^-1 dot Y
        if self. iK is None:
            self.iK = S(np.zeros((self.E,self.N,self.N),dtype='float64'), name="%s>iK"%(self.name))
        if self.L is None:
            self.L = S(np.zeros((self.E,self.N,self.N),dtype='float64'), name="%s>L"%(self.name))
        if self.beta is None:
            self.beta = S(np.zeros((self.E,self.N),dtype='float64'), name="%s>beta"%(self.name))
        if self.X_cov is not None and self.nigp is None:
            self.nigp = S(np.zeros((self.E,self.N),dtype='float64'), name="%s>nigp"%(self.name))

        N = self.X.shape[0].astype('float64')
        
        def log_marginal_likelihood(Y,loghyp,i,X,EyeN,nigp=None,y_var=None):
            # initialise the (before compilation) kernel function
            loghyps = (loghyp[:idims+1],loghyp[idims+1])
            kernel_func = partial(cov.Sum, loghyps, self.covs)

            # We initialise the kernel matrices (one for each output dimension)
            K = kernel_func(X)
            # add the contribution from the input noise
            if nigp:
                K += tt.diag(nigp[i])
            # add the contribution from the output uncertainty (acts as weight)
            if y_var:
                K += tt.diag(y_var[i])
            L = cholesky(K)
            iK = solve_upper_triangular(L.T, solve_lower_triangular(L,EyeN))
            Yc = solve_lower_triangular(L,Y)
            beta = solve_upper_triangular(L.T,Yc)

            # And finally, the negative log marginal likelihood ( again, one for each dimension; although we could share
            # the loghyperparameters across all output dimensions and train the GPs jointly)
            loss = 0.5*(Yc.T.dot(Yc) + 2*tt.sum(tt.log(tt.diag(L))) + N*tt.log(2*np.pi) )

            return loss,iK,L,beta
        
        nseq = [self.X,tt.eye(self.X.shape[0])]
        if self.nigp:
            nseq.append(self.nigp)
        if self.Y_var:
            nseq.append(self.Y_var.T)
        (loss,iK,L,beta),updts = theano.scan(fn=log_marginal_likelihood, sequences=[self.Y.T,self.loghyp,tt.arange(self.X.shape[0])], non_sequences=nseq, allow_gc=False, name="%s>logL_scan"%(self.name))

        iK = tt.unbroadcast(iK,0) if iK.broadcastable[0] else iK
        L = tt.unbroadcast(L,0) if L.broadcastable[0] else L
        beta = tt.unbroadcast(beta,0) if beta.broadcastable[0] else beta
    
        if cache_vars:
            # we are going to save the intermediate results in the following shared variables, so we can use them during prediction without having to recompute them
            updts =[(self.iK,iK),(self.L,L),(self.beta,beta)]
        else:
            self.iK = iK 
            self.L = L 
            self.beta = beta
            updts=None

        # we add some penalty to avoid having parameters that are too large
        if self.snr_penalty is not None:
            penalty_params = {'log_snr': np.log(1000), 'log_ls': np.log(100), 'log_std': tt.log(self.X.std(0)*(N/(N-1.0))), 'p': 30}
            loss += self.snr_penalty(self.loghyp)

        # Compute the gradients for the sum of loss for all output dimensions
        dloss = tt.grad(loss.sum(),self.loghyp)

        # Compile the theano functions
        if compile_funcs:
            utils.print_with_stamp('Compiling full GP training loss function',self.name)
            self.loss_fn = F((),loss,name='%s>loss'%(self.name), profile=self.profile, mode=self.compile_mode, allow_input_downcast=True, updates=updts)
            utils.print_with_stamp('Compiling gradient of full GP training loss function',self.name)
            self.dloss_fn = F((),(loss,dloss),name='%s>dloss'%(self.name), profile=self.profile, mode=self.compile_mode, allow_input_downcast=True, updates=updts)
        self.state_changed = True # for saving
    
    def init_predict(self, init_loss=True, compile_funcs=True):
        if init_loss and self.loss_fn is None:
            self.init_loss()

        utils.print_with_stamp('Initialising expression graph for prediction',self.name)
        # Note that this handles n_samples inputsa
        # initialize variable for input vector ( input mean in the case of uncertain inputs )
        mx = tt.vector('mx')
        Sx = tt.matrix('Sx')
        # initialize variable for input covariance 
        input_vars = [mx] if not self.uncertain_inputs else [mx,Sx]
        
        # get prediction
        output_vars = self.predict_symbolic(mx,Sx)
        prediction = []
        for o in output_vars:
            if o is not None:
                prediction.append(o)
        
        # compile prediction
        utils.print_with_stamp('Compiling mean and variance of prediction',self.name)
        self.predict_fn = F(input_vars,prediction,name='%s>predict_'%(self.name), profile=self.profile, mode=self.compile_mode, allow_input_downcast=True)
        self.state_changed = True # for saving

    def predict_symbolic(self,mx,Sx):
        idims = self.D
        odims = self.E

        # compute the mean and variance for each output dimension
        def predict_odim(L,beta,loghyp,X,mx):
            loghyps = (loghyp[:idims+1],loghyp[idims+1])
            kernel_func = partial(cov.Sum, loghyps, self.covs)

            k = kernel_func(mx[None,:],X)
            mean = k.dot(beta)
            kc = solve_lower_triangular(L,k.flatten())
            variance = kernel_func(mx[None,:],all_pairs=False) - kc.dot(kc)

            return mean, variance
        
        (M,S), updts = theano.scan(fn=predict_odim, sequences=[self.L,self.beta,self.loghyp], non_sequences=[self.X,mx], allow_gc = False,name='%s>predict_scan'%(self.name))

        # reshape output variables
        M = M.flatten()
        S = tt.diag(S.flatten())
        V = tt.zeros((self.D,self.E))

        return M,S,V
    
    def predict(self,mx,Sx = None):
        predict = None
        if self.predict_fn is None or self.should_recompile:
            self.init_predict()
        predict = self.predict_fn

        odims = self.E
        idims = self.D
        res = None
        if self.uncertain_inputs:
            if Sx is None:
                Sx = np.zeros((idims,idims))
            res = predict(mx, Sx)
        else:
            res = predict(mx)
        return res
    
    def loss(self,loghyp):
        self.set_params({'loghyp': loghyp})
        if self.nigp:
            # update the nigp parameter using the derivative of the mean function
            dM2 = self.dM2_fn()
            nigp = ((dM2[:,:,:,None]*self.X_cov[None]).sum(2)*dM2).sum(-1)
            self.nigp.set_value(nigp)

        loss,dloss = self.dloss_fn()
        loss = loss.sum()
        dloss = dloss.flatten()
        # on a 64bit system, scipy optimize complains if we pass a 32 bit float
        res = (loss.astype(np.float64), dloss.astype(np.float64))
        utils.print_with_stamp('%s'%(str(res[0])),self.name,True)
        if loss < self.besthyp[0]:
            self.besthyp = [loss, loghyp]

        return res

    def train(self):
        if self.loss_fn is None or self.should_recompile:
            self.init_loss()

        if self.nigp and not hasattr(self, 'dM2_fn'):
            idims = self.D
            utils.print_with_stamp('Compiling derivative of mean function at training inputs',self.name)
            # we need to evaluate the derivative of the mean function at the training inputs
            def dM2_f_i(mx,beta,loghyp,X):
                loghyps = (loghyp[:idims+1],loghyp[idims+1])
                kernel_func = partial(cov.Sum, loghyps, self.covs)
                k = kernel_func(mx[None,:],X).flatten()
                mean = k.dot(beta)
                dmean = tt.jacobian(mean.flatten(),mx)
                return tt.square(dmean.flatten())
            
            def dM2_f(beta,loghyp,X):
                # iterate over training inputs
                dM2_o, updts = theano.scan(fn=dM2_f_i, sequences=[X], non_sequences=[beta,loghyp,X], allow_gc = False)
                return dM2_o

            # iterate over output dimensions
            dM2, updts = theano.scan(fn=dM2_f, sequences=[self.beta,self.loghyp], non_sequences=[self.X], allow_gc = False)

            self.dM2_fn = F((),dM2,name='%s>dM2'%(self.name), profile=self.profile, mode=self.compile_mode, allow_input_downcast=True, updates=updts)


        loghyp0 = self.loghyp.eval()
        utils.print_with_stamp('Current hyperparameters:\n%s'%(loghyp0),self.name)
        utils.print_with_stamp('loss: %s'%(np.array(self.loss_fn())),self.name)
        m_loss = utils.MemoizeJac(self.loss)
        self.n_evals=0
        min_methods = self.min_method if type(self.min_method) is list else [self.min_method]
        min_methods.extend([m for m in DETERMINISTIC_MIN_METHODS if m != self.min_method])
        self.besthyp = [np.array(self.loss_fn()).sum(), loghyp0]
        for m in min_methods:
            try:
                utils.print_with_stamp("Using %s optimizer"%(m),self.name)
                opt_res = minimize(m_loss, loghyp0, jac=m_loss.derivative, method=m, tol=self.conv_thr, options={'maxiter': self.max_evals, 'maxcor': 100, 'maxls':30})
                break
            except ValueError:
                print ''
                utils.print_with_stamp("Optimization with %s failed"%(m),self.name)
                loghyp0 = self.besthyp[1]

        print ''
        loghyp = opt_res.x.reshape(loghyp0.shape)
        self.state_changed = not np.allclose(loghyp0,loghyp,1e-6,1e-9)
        self.set_params({'loghyp': loghyp})
        utils.print_with_stamp('New hyperparameters:\n%s'%(self.loghyp.eval()),self.name)
        utils.print_with_stamp('loss: %s'%(np.array(self.loss_fn())),self.name)
        self.trained = True

class GP_UI(GP):
    ''' Gaussian process with uncertain inputs (Deisenroth et al  2009)'''
    def __init__(self, X_dataset=None, Y_dataset=None, name = 'GP_UI', idims=None, odims=None, profile=False, **kwargs):
        super(GP_UI, self).__init__(X_dataset,Y_dataset,name=name,idims=idims,odims=odims,profile=profile,uncertain_inputs=True, **kwargs)

    def predict_symbolic(self,mx,Sx):
        idims = self.D
        odims = self.E

        #centralize inputs 
        zeta = self.X - mx
        
        # initialize some variables
        sf2 = tt.exp(2*self.loghyp[:,idims])
        eyeE = tt.tile(tt.eye(idims),(odims,1,1))
        lscales = tt.exp(self.loghyp[:,:idims])
        iL = eyeE/lscales.dimshuffle(0,1,'x')

        # predictive mean
        inp = iL.dot(zeta.T).transpose(0,2,1) 
        iLdotSx = iL.dot(Sx.astype('float64')) # force the matrix inverse to be done with double precision
        B = tt.stack([iLdotSx[i].dot(iL[i]) for i in xrange(odims)]) + tt.eye(idims)                              #TODO vectorize this
        #t = tt.stack([inp[i].dot(matrix_inverse(B[i])) for i in xrange(odims)])      # E x N x D
        t = tt.stack([solve(B[i].T, inp[i].T).T for i in xrange(odims)])      # E x N x D
        c = sf2/tt.sqrt(tt.stack([det(B[i]) for i in xrange(odims)]))
        l = tt.exp(-0.5*tt.sum(inp*t,2))
        lb = l*self.beta # beta should have been precomputed in init_loss # E x N dot E x N
        M = tt.sum(lb,1)*c
        
        # input output covariance
        tiL = tt.stack([t[i].dot(iL[i]) for i in xrange(odims)])
        #V = Sx.dot(tt.stack([tiL[i].T.dot(lb[i]) for i in xrange(odims)]).T*c)
        V = tt.stack([tiL[i].T.dot(lb[i]) for i in xrange(odims)]).T*c

        # predictive covariance
        logk = 2*self.loghyp[:,None,idims] - 0.5*tt.sum(inp*inp,2)
        logk_r = logk.dimshuffle(0,'x',1)
        logk_c = logk.dimshuffle(0,1,'x')
        Lambda = tt.square(iL)
        R = tt.dot((Lambda.dimshuffle(0,'x',1,2) + Lambda).transpose(0,1,3,2),Sx.astype('float64').T).transpose(0,1,3,2) + tt.eye(idims) # again forcing the matrix inverse to be done with double precision
        z_= Lambda.dot(zeta.T).transpose(0,2,1) 
        
        M2 = tt.zeros((self.E,self.E),dtype='float64')
        # initialize indices
        indices = [ tt.as_index_variable(idx) for idx in np.triu_indices(self.E) ]

        def second_moments(i,j,M2,beta,iK,sf2,R,logk_c,logk_r,z_,Sx):
            # This comes from Deisenroth's thesis ( Eqs 2.51- 2.54 )
            Rij = R[i,j]
            #n2 = logk_c[i] + logk_r[j] + utils.maha(z_[i],-z_[j],0.5*matrix_inverse(Rij).dot(Sx))
            n2 = logk_c[i] + logk_r[j] + utils.maha(z_[i],-z_[j],0.5*solve(Rij,Sx))
            Q = tt.exp( n2 )/tt.sqrt(det(Rij))
            # Eq 2.55
            m2 = matrix_dot(beta[i], Q, beta[j])
            
            m2 = theano.ifelse.ifelse(tt.eq(i,j), m2 - tt.sum(iK[i]*Q) + sf2[i], m2)
            M2 = tt.set_subtensor(M2[i,j], m2)
            M2 = theano.ifelse.ifelse(tt.eq(i,j), M2 , tt.set_subtensor(M2[j,i], m2))
            return M2

        M2_,updts = theano.scan(fn=second_moments, 
                               sequences=indices,
                               outputs_info=[M2],
                               non_sequences=[self.beta,self.iK,sf2,R,logk_c,logk_r,z_,Sx],
                               allow_gc=False,
                               name="%s>M2_scan"%(self.name))
        M2 = M2_[-1]
        S = M2 - tt.outer(M,M)

        return M,S,V

class RBFGP(GP_UI):
    ''' RBF network (GP with uncertain inputs/deterministic outputs)'''
    def __init__(self, X_dataset=None, Y_dataset=None, idims=None, odims=None, sat_func=None, name = 'RBFGP',profile=False, **kwargs):
        self.sat_func = sat_func
        if self.sat_func is not None:
            name += '_sat'
        self.loghyp_full=None
        super(RBFGP, self).__init__(X_dataset,Y_dataset,idims=idims,odims=odims,name=name,profile=profile, **kwargs)
        
        # register additional variables for saving
        self.register(['sat_func'])
        self.register(['iK','beta','L'])

    def predict_symbolic(self,mx,Sx):
        idims = self.D
        odims = self.E

        #centralize inputs 
        zeta = self.X - mx
        
        # initialize some variables
        sf2 = tt.exp(2*self.loghyp[:,idims])
        eyeE = tt.tile(tt.eye(idims),(odims,1,1))
        lscales = tt.exp(self.loghyp[:,:idims])
        iL = eyeE/lscales.dimshuffle(0,1,'x')

        # predictive mean
        inp = iL.dot(zeta.T).transpose(0,2,1) 
        iLdotSx = iL.dot(Sx.astype('float64')) # force the matrix inverse to be done with double precision
        B = tt.stack([iLdotSx[i].dot(iL[i]) for i in xrange(odims)]) + tt.eye(idims)   #TODO vectorize this
        #t = tt.stack([inp[i].dot(matrix_inverse(B[i])) for i in xrange(odims)])      # E x N x D
        t = tt.stack([solve(B[i].T, inp[i].T).T for i in xrange(odims)])      # E x N x D
        c = sf2/tt.sqrt(tt.stack([det(B[i]) for i in xrange(odims)]))
        l = tt.exp(-0.5*tt.sum(inp*t,2))
        lb = l*self.beta # beta should have been precomputed in init_loss # E x N
        M = tt.sum(lb,1)*c
        
        # input output covariance
        tiL = tt.stack([t[i].dot(iL[i]) for i in xrange(odims)])
        #V = Sx.dot(tt.stack([tiL[i].T.dot(lb[i]) for i in xrange(odims)]).T*c)
        V = tt.stack([tiL[i].T.dot(lb[i]) for i in xrange(odims)]).T*c

        # predictive covariance
        logk = 2*self.loghyp[:,None,idims] - 0.5*tt.sum(inp*inp,2)
        logk_r = logk.dimshuffle(0,'x',1)
        logk_c = logk.dimshuffle(0,1,'x')
        Lambda = tt.square(iL)
        R = tt.dot((Lambda.dimshuffle(0,'x',1,2) + Lambda).transpose(0,1,3,2),Sx.astype('float64').T).transpose(0,1,3,2) + tt.eye(idims) # again forcing the matrix inverse to be done with double precision
        z_= Lambda.dot(zeta.T).transpose(0,2,1) 
        
        if self.E == 1:
            # for some reason, compiling the policy gradients breaks when the output dimension of this class is one
            #  TODO: do the same in the other classes that compute second_moments
            # with a scan loop,
            # This comes from Deisenroth's thesis ( Eqs 2.51- 2.54 )
            Rij = R[0,0]
            #n2 = logk_c[0] + logk_r[0] + utils.maha(z_[0],-z_[0],0.5*matrix_inverse(Rij).dot(Sx))
            n2 = logk_c[0] + logk_r[0] + utils.maha(z_[0],-z_[0],0.5*solve(Rij,Sx))
            Q = tt.exp( n2 )/tt.sqrt(det(Rij))
            # Eq 2.55
            m2 = matrix_dot(self.beta[0],Q,self.beta[0].T)
            m2 = m2 + 1e-6
            M2 = tt.stack([m2])
        else:
            M2 = tt.zeros((self.E,self.E),dtype='float64')
            # initialize indices
            indices = [ tt.as_index_variable(idx) for idx in np.triu_indices(self.E) ]

            def second_moments(i,j,M2,beta,R,logk_c,logk_r,z_,Sx):
                # This comes from Deisenroth's thesis ( Eqs 2.51- 2.54 )
                Rij = R[i,j]
                #n2 = logk_c[i] + logk_r[j] + utils.maha(z_[i],-z_[j],0.5*matrix_inverse(Rij).dot(Sx))
                n2 = logk_c[i] + logk_r[j] + utils.maha(z_[i],-z_[j],0.5*solve(Rij,Sx))
                Q = tt.exp( n2 )/tt.sqrt(det(Rij))
                # Eq 2.55
                m2 = matrix_dot(beta[i], Q, beta[j])
                
                m2 = theano.ifelse.ifelse(tt.eq(i,j), m2 + 1e-6 , m2)
                M2 = tt.set_subtensor(M2[i,j], m2)
                M2 = theano.ifelse.ifelse(tt.eq(i,j), M2 , tt.set_subtensor(M2[j,i], m2))
                return M2

            M2_,updts = theano.scan(fn=second_moments, 
                                sequences=indices,
                                outputs_info=[M2],
                                non_sequences=[self.beta,R,logk_c,logk_r,z_,Sx],
                                allow_gc=False,
                                name="%s>M2_scan"%(self.name))
            M2 = M2_[-1]

        S = M2 - tt.outer(M,M)

        # apply saturating function to the output if available
        if self.sat_func is not None:
            # saturate the output
            M,S,U = self.sat_func(M,S)
            # compute the joint input output covariance
            V = V.dot(U)

        return M,S,V

