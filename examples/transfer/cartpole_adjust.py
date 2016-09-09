import atexit
import os, sys
import numpy as np
from functools import partial

from ghost.regression.GP import SSGP_UI,SSGP,GP,GP_UI
from ghost.learners.ExperienceDataset import ExperienceDataset
from ghost.transfer.trajectory_matching import TrajectoryMatching
from ghost.control import RBFPolicy
from ghost.control import AdjustedPolicy

from shell.cartpole import Cartpole, CartpoleDraw, cartpole_loss
import shell.cartpole

from shell.plant import SerialPlant

import utils
#np.random.seed(31337)
np.set_printoptions(linewidth=500)

if __name__ == '__main__':
    N = 100
    J = 2
    simulation = True
    base_dir = utils.get_output_dir()
    source_dir = os.path.join(base_dir,'cartpole')
    target_dir = os.path.join(base_dir,'target')
    # SOURCE DOMAIN 
    utils.set_output_dir(source_dir)
    # load source experience
    source_experience = ExperienceDataset(filename='PILCO_SSGP_UI_Cartpole_RBFPolicy_sat_dataset')
    #load source policy
    source_policy = RBFPolicy(filename='RBFPolicy_sat_5_1_cpu_float64')
    
    # TARGET DOMAIN
    utils.set_output_dir(target_dir)
    target_params = shell.cartpole.default_params()
    # policy
    target_params['dynmodel_class'] = GP
    target_params['policy_class'] = AdjustedPolicy
    target_params['params']['policy']['adjustment_model_class'] = GP
    print target_params.keys()

    # initialize target plant
    if not simulation:
        target_params['plant_class'] = SerialPlant
        target_params['params']['plant']['maxU'] = target_params['params']['policy']['maxU']
        target_params['params']['plant']['state_indices'] = [0,2,3,1]
        target_params['params']['plant']['baud_rate'] = 4000000
        target_params['params']['plant']['port'] = '/dev/ttyACM0'
    else:
        # TODO get these as command line arguments
        target_params['params']['plant']['params'] = {'l': 0.5, 'm': 1.5, 'M': 1.5, 'b': 0.1, 'g': 9.82}
        target_params['params']['cost']['pendulum_length'] = target_params['params']['plant']['params']['l']

    target_params['params']['source_policy'] = source_policy
    target_params['params']['source_experience'] = source_experience

    # initialize trajectory matcher
    tm = TrajectoryMatching(**target_params)
    atexit.register(tm.stop)
    draw_cp = CartpoleDraw(tm.plant)
    draw_cp.start()
    atexit.register(draw_cp.stop)
    
    for i in xrange(N):
        # sample target trajecotry
        tm.plant.reset_state()
        tm.apply_controller()

        tm.train_inverse_dynamics()
        tm.train_adjustment()

    sys.exit(0)
