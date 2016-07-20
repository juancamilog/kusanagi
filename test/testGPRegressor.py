from ghost.regression.GP import *
import argparse
from matplotlib import pyplot as plt
from utils import gTrig_np, gTrig2_np, print_with_stamp
from scipy.signal import convolve2d
from scipy.stats import multivariate_normal
from time import time
from theano import d3viz
from theano.printing import pydotprint

np.set_printoptions(linewidth=500, precision=17, suppress=True)

def test_func1(X):
    return 100*np.exp(-0.5*(np.sum((X**2),1)))*np.sin(X.sum(1))

def build_dataset(idims=9,odims=6,angi=[],f=test_func1,n_train=500,n_test=50,rand_seed=None):
    if rand_seed is not None:
        np.random.seed(rand_seed)
    #  ================== train dataset ==================
    # sample training points
    x_train = 5*(np.random.rand(n_train,idims) - 0.5)
    # generate the output at the training points
    y_train = np.empty((n_train,odims))
    for i in xrange(odims):
        y_train[:,i] =  (i+1)*f(x_train) + 0.01*(np.random.rand(n_train)-0.5)
    x_train = gTrig_np(x_train, angi)
    
    #  ================== test  dataset ==================
    # generate testing points
    kk = 0.01*convolve2d(np.array([[1,2,3,2,1]]),np.array([[1,2,3,2,1]]).T)/9.0;
    s_test = convolve2d(np.eye(idims),kk,'same')
    s_test = np.tile(s_test,(n_test,1)).reshape(n_test,idims,idims)
    x_test = 5*(np.random.rand(n_test,idims) - 0.5)
    # generate the output at the test points
    y_test = np.empty((n_test,odims))
    for i in xrange(odims):
        y_test[:,i] =  (i+1)*f(x_test) + 0.01*(np.random.rand(n_test)-0.5)
    if len(angi)>0:
        x_test,s_test = gTrig2_np(x_test,s_test, angi, idims)

    return (x_train,y_train),(x_test,y_test,s_test)

def build_GP(idims=9, odims=6, gp_type='GP', profile=theano.config.profile):
    if gp_type == 'GP_UI':
        gp = GP_UI(idims=idims,odims=odims,profile=profile)
    elif gp_type == 'RBFGP':
        gp = RBFGP(idims=idims,odims=odims,profile=profile)
    elif gp_type == 'SPGP':
        gp = SPGP(idims=idims,odims=odims,profile=profile,n_basis=100)
    elif gp_type == 'SPGP_UI':
        gp = SPGP_UI(idims=idims,odims=odims,profile=profile,n_basis=100)
    elif gp_type == 'SSGP':
        gp = SSGP_UI(idims=idims,odims=odims,profile=profile,n_basis=100)
    elif gp_type == 'SSGP_UI':
        gp = SSGP_UI(idims=idims,odims=odims,profile=profile,n_basis=100)
    else:
        gp = GP(idims=idims,odims=odims,profile=profile)
    return gp

def write_profile_files(gp):
    d3viz.d3viz(gp.dnlml, 'dnlml.html')
    d3viz.d3viz(gp.predict_fn, 'predict.html')

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--gp_type', nargs='?', help='the name of the GP regressor class (GP,GP_UI,SPGP,SPGP_UI,SSGP,SSGP_UI). Default: GP_UI.', default='GP_UI')
    parser.add_argument('--n_train', nargs='?', type=int, help='Number of training samples. Default: 500.', default=500)
    parser.add_argument('--n_test', nargs='?', type=int, help='Number of testing samples. Default: 50', default=50)
    parser.add_argument('--idims', nargs='?', type=int, help='Input dimensions. Default: 4', default=4)
    parser.add_argument('--odims', nargs='?', type=int, help='Output dimensions. Default: 2', default=2)
    args = parser.parse_args()

    idims = args.idims
    odims = args.odims
    n_train = args.n_train
    n_test = args.n_test
    utils.print_with_stamp("Building test dataset",'main')
    train_dataset,test_dataset = build_dataset(idims=idims,odims=odims,n_train=n_train,n_test=n_test, rand_seed=31337)
    utils.print_with_stamp("Building regressor",'main')
    gp = build_GP(idims,odims,gp_type=args.gp_type,profile=theano.config.profile)
    gp.set_dataset(train_dataset[0],train_dataset[1])

    gp.train()
    gp.save()

    utils.print_with_stamp("Testing regressor",'main')
    test_mX = test_dataset[0]
    test_sX = test_dataset[2]
    test_Y = test_dataset[1]
    errors = []
    probs = []
    for i in xrange(n_test):
        ret = gp.predict(test_mX[i], test_sX[i])
        print '============%04d============'%(i)
        print 'Test Point:\n%s'%(test_mX[i])
        print 'Ground Truth:\n%s'%(test_Y[i])
        print 'Mean Prediction:\n%s'%(ret[0])
        print 'Prediction Covariance:\n%s'%(ret[1])
        print 'Input/Output Covariance:\n%s'%(ret[2])
        errors.append(np.sqrt(((ret[0]-test_Y[i])**2).sum()))
        print 'Error:\t%f'%(errors[-1])
        probs.append(np.log(multivariate_normal.pdf(test_Y[i],mean=ret[0],cov=ret[1])))
        print 'Log Probability of Ground Truth:\t%f'%(probs[-1])

    errors = np.array(errors)
    probs = np.array(probs)
    print '============================='
    print 'Min/Max/Mean Prediction Error:\t %f / %f / %f'%(errors.min(),errors.max(),errors.mean())
    print 'Min/Max/Mean Log Probablity:\t %f / %f / %f'%(probs.min(),probs.max(),probs.mean())


