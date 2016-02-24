import signal,sys
import numpy as np
from functools import partial
from ghost.learners.PILCO import PILCO
from shell.toy.cartpole import Cartpole, CartpoleDraw, cartpole_loss
from ghost.control import RandPolicy, RBFPolicy

if __name__ == '__main__':
    #np.random.seed(31337)
    np.set_printoptions(linewidth=500)
    # initliaze plant
    dt = 0.1                                                         # simulation time step
    model_parameters ={}                                             # simulation parameters
    model_parameters['l'] = 0.5
    model_parameters['m'] = 0.5
    model_parameters['M'] = 0.5
    model_parameters['b'] = 0.1
    model_parameters['g'] = 9.82
    x0 = [0,0,0,0]                                                   # initial state mean
    S0 = np.eye(4)*(0.1**2)                                          # initial state covariance
    measurement_noise = np.diag(np.ones(len(x0))*0.01**2)            # model measurement noise (randomizes the output of the plant)
    plant = Cartpole(model_parameters,x0,S0,dt,measurement_noise)
    draw_cp = CartpoleDraw(plant,0.033)                              # initializes visualization
    draw_cp.start()
    def signal_handler(signal, frame):                               # initialize signal handler to capture ctrl-c
        draw_cp.stop()
        plant.stop()
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)

    # initialize policy
    angle_dims = [3]
    p = RBFPolicy(x0,S0,[10],10, angle_dims)

    # initialize cost function
    cost_parameters = {}
    cost_parameters['angle_dims'] = angle_dims
    cost_parameters['target'] = [0,0,0,np.pi]
    cost_parameters['width'] = 0.25
    cost_parameters['expl'] = 0.0
    cost_parameters['pendulum_length'] = model_parameters['l']
    cost = partial(cartpole_loss, params=cost_parameters)

    # initialize learner
    T = 8.0                                                          # controller horizon
    J = 25                                                           # number of random initial trials
    learner = PILCO(plant, p, cost, angle_dims, async_plant=False)
    
    # gather data with random trials
    for i in xrange(J):
        plant.reset_state()
        learner.apply_controller(H=T)

    draw_cp.stop()
