
class KLMatching(PILCO):
    def __init__(self, params, plant_class, policy_class, cost_func=None, viz_class=None, dynmodel_class=kreg.GP_UI, invdynmodel_class=kreg.GP_UI, experience = None, async_plant=False, name='TrajectoryMatching', wrap_angles=False, filename_prefix=None):
        super(KLMatching, self).__init__(params, plant_class, policy_class, cost_func,viz_class, dynmodel_class,  experience, async_plant, name, filename_prefix)
