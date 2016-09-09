import atexit
import signal,sys,os
#sys.path.append('/home/adaptation/achatr/Desktop/Summer2016/PILCO_clone/kusanagi')
import numpy as np
import utils
from functools import partial
from ghost.regression.GP import SSGP_UI
from ghost.learners.PILCO import PILCO
from shell.cartpole import Cartpole, CartpoleDraw, cartpole_loss, default_params
from ghost.control import RBFPolicy
from utils import plot_results
#np.random.seed(31337)
np.set_printoptions(linewidth=500)

if __name__ == '__main__':
    # setup output directory
    utils.set_output_dir(os.path.join(utils.get_output_dir(),'cartpole'))
    J = 4                                                                   # number of random initial trials
    N = 100                                                                 # learning iterations
    learner_params = default_params()
    # initialize learner
    learner = PILCO(**learner_params)
    learner.load()
    atexit.register(learner.stop)
    draw_cp = CartpoleDraw(learner.plant)
    draw_cp.start()
    atexit.register(draw_cp.stop)

    # gather data with random trials
    for i in xrange(J):
        learner.plant.reset_state()
        learner.apply_controller()
    
    sys.exit(0)
