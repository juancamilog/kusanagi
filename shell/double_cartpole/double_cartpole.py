import numpy as np
import theano
from shell.plant import ODEPlant, PlantDraw
from ghost.cost import quadratic_saturating_loss
from utils import print_with_stamp, gTrig_np, gTrig2
from matplotlib import pyplot as plt

def double_cartpole_loss(mx,Sx,params, loss_func=quadratic_saturating_loss):
    angle_dims = params['angle_dims']
    cw = params['width']
    if type(cw) is not list:
        cw = [cw]
    b = params['expl']
    ell1,ell2 = params['pendulum_lengths']
    target = np.array(params['target'])
    D = target.size
    
    #convert angle dimensions
    targeta = gTrig_np(target,angle_dims).flatten()
    Da = targeta.size
    mxa,Sxa,Ca = gTrig2(mx,Sx,angle_dims,D) # angle dimensions are removed, and their complex representation is appended
    # build cost scaling function
    cost_dims = np.hstack([0, np.arange(Da-2*len(angle_dims),Da)])[:,None]  # these are the dimensions used to comptue the cost ( x, sin(theta1), cos(theta1), sin(theta2), cos(theta2) )
    Q = np.zeros((Da,Da))
    C = np.array([ [ 1, -ell1,    0, -ell2,    0], 
                   [ 0,     0, ell1,     0, ell2]]);
    Q[cost_dims,cost_dims.T] = C.T.dot(C)
    
    M_cost = [] ; S_cost = []
    
    # total cost is the sum of costs with different widths
    for c in cw:
        loss_params = {}
        loss_params['target'] = targeta
        loss_params['Q'] = Q/c**2
        m_cost, s_cost = loss_func(mxa,Sxa,loss_params)
        if b is not None:
            m_cost += b*theano.tensor.sqrt(s_cost) # UCB  exploration term
        M_cost.append(m_cost)
        S_cost.append(s_cost)
    
    return sum(M_cost), sum(S_cost)

class DoubleCartpole(ODEPlant):
    def __init__(self, params, x0, S0=None, dt=0.01, noise=None, name='DoubleCartpole', integrator='dopri5', atol=1e-12, rtol=1e-12):
        super(DoubleCartpole, self).__init__(params, x0, S0, dt=dt, noise=noise, name=name, integrator=integrator, atol=atol, rtol=rtol)

    def dynamics(self,t,z):
        m1 = self.params['m1']
        m2 = self.params['m2']
        m3 = self.params['m3']
        l2 = self.params['l2']
        l3 = self.params['l3']
        b = self.params['b']
        g = self.params['g']

        f = self.u if self.u is not None else np.array([0])

        f = f.flatten()

        sz4 = np.sin(z[4]); cz4 = np.cos(z[4]);
        sz5 = np.sin(z[5]); cz5 = np.cos(z[5]);
        cz4m5 = np.cos(z[4] - z[5])
        sz4m5 = np.sin(z[4] - z[5])
        a0 = m2+2*m3
        a1 = m3*l3
        a2 = l2*(z[2]*z[2])
        a3 = a1*(z[3]*z[3])

        A = np.array([[ 2*(m1+m2+m3), -a0*l2*cz4,       -a1*cz5    ],
                      [-3*a0*cz4,     (2*a0+2*m3)*l2,   3*a1*cz4m5 ],
                      [-3*cz5,         3*l2*cz4m5,       2*l3      ]])
        b = np.array([ 2*f[0]-2*b*z[1]-a0*a2*sz4-a3*sz5,
                       3*a0*g*sz4 - 3*a3*sz4m5,
                       3*a2*sz4m5 + 3*g*sz5]).flatten()
        
        x = np.linalg.solve(A,b)

        dz = np.zeros((6,))
        dz[0] = z[1]
        dz[1] = x[0]
        dz[2] = x[1]
        dz[3] = x[2]
        dz[4] = z[2]
        dz[5] = z[3]

        return dz


class DoubleCartpoleDraw(PlantDraw):
    def __init__(self, cartpole_plant, refresh_period=100, name='DoubleCartpoleDraw'):
        super(DoubleCartpoleDraw, self).__init__(cartpole_plant, refresh_period,name)
        m1 = self.plant.params['m1']
        m2 = self.plant.params['m2']
        m3 = self.plant.params['m3']
        l2 = self.plant.params['l2']
        l3 = self.plant.params['l3']
        b = self.plant.params['b']
        g = self.plant.params['g']

        self.body_h = 0.5*np.sqrt( m1 )
        self.mass_r1 = 0.05*np.sqrt( m2 ) # distance to corner of bounding box
        self.mass_r2 = 0.05*np.sqrt( m3 ) # distance to corner of bounding box

        self.center_x = 0
        self.center_y = 0

        # initialize the patches to draw the cartpole
        self.body_rect = plt.Rectangle( (self.center_x-0.5*self.body_h, self.center_y-0.125*self.body_h), self.body_h, 0.25*self.body_h, facecolor='black')
        self.pole_line1 = plt.Line2D((self.center_x, 0), (self.center_y, l2), lw=2, c='r')
        self.mass_circle1 = plt.Circle((0, l2), self.mass_r1, fc='y')
        self.pole_line2 = plt.Line2D((self.center_x, 0), (l2, l3), lw=2, c='r')
        self.mass_circle2 = plt.Circle((0, l2+l3), self.mass_r2, fc='y')

    def init_artists(self):
        self.ax.add_patch(self.body_rect)
        self.ax.add_patch(self.mass_circle1)
        self.ax.add_line(self.pole_line1)
        self.ax.add_patch(self.mass_circle2)
        self.ax.add_line(self.pole_line2)

    def update(self, state, t):
        l2 = self.plant.params['l2']
        l3 = self.plant.params['l3']

        body_x = self.center_x + state[0]
        body_y = self.center_y
        mass1_x = -l2*np.sin(state[4]) + body_x
        mass1_y = l2*np.cos(state[4]) + body_y
        mass2_x = -l3*np.sin(state[5]) + mass1_x
        mass2_y = l3*np.cos(state[5]) + mass1_y

        self.body_rect.set_xy((body_x-0.5*self.body_h,body_y-0.125*self.body_h))
        self.pole_line1.set_xdata(np.array([body_x,mass1_x]))
        self.pole_line1.set_ydata(np.array([body_y,mass1_y]))
        self.pole_line2.set_xdata(np.array([mass1_x,mass2_x]))
        self.pole_line2.set_ydata(np.array([mass1_y,mass2_y]))
        self.mass_circle1.center = (mass1_x,mass1_y)
        self.mass_circle2.center = (mass2_x,mass2_y)

        return (self.body_rect, self.pole_line1, self.mass_circle1, self.pole_line2, self.mass_circle2)