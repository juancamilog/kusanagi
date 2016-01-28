from ghost.learners.PILCO import PILCO
from shell.toy.cartpole import Cartpole, CartpoleDraw, CartpoleCost
from ghost.control import RandPolicy, RBFPolicy
import numpy as np
from threading import Thread
from time import sleep,time
from util import augment
import utils
import sys

if __name__ == '__main__':
    np.set_printoptions(linewidth=200, precision=10, suppress=True)
    dt = 0.1
    model_parameters ={}
    model_parameters['l'] = 0.5
    model_parameters['m'] = 0.5
    model_parameters['M'] = 0.5
    model_parameters['b'] = 0.1
    model_parameters['g'] = 9.82

    x0 = [0,0,0,np.pi]
    S0 = np.eye(4)*0.001
    target = [0,0,0,np.pi]
    angle_dims = [3]


    p1 = RandPolicy([0.001])
    p2 = RBFPolicy(x0,0.1*np.eye(len(x0)),[10],10, angle_dims)
    cost = CartpoleCost(target,model_parameters['l'], angle_dims)
    plant = Cartpole(model_parameters,x0,S0,dt)
    
    draw_cp = CartpoleDraw(plant,0.033)
    draw_cp.start()
    learner = PILCO(plant, p2, cost, angle_dims)

    # testing single PILCO loop
    for i in xrange(5):
        plant.reset_state()
        learner.apply_controller(H=4)

    learner.train_dynamics()
    learner.value(H=4)
    #learner.train_policy()
    
    # saving dataset for external tests
    x = np.array(learner.experience.states)
    u = np.array(learner.experience.actions)
    # inputs are states, concatenated with actions (except for the last entry)
    X = np.hstack((x[:-1],u[:-1]))
    # outputs are next states
    Y =  x[1:]
    np.savez('experience.npz',X=X,Y=Y)

    draw_cp.stop()
