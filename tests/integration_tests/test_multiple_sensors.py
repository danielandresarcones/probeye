"""
Linear model in time and space with three different noise models
--------------------------------------------------------------------------------
The model equation is y = A * x + B * t + c with A, B, c being the model prms.
while x and t represent position and time respectively. From the three model
parameters A and B are latent ones while c is a constant. Measurements are made
at three different positions (x-values) each of which is associated with an own
zero-mean, uncorrelated normal noise model with the std. deviations to infer.
This results in five latent parameters (parameters to infer). The problem
is solved via sampling by means of emcee and pyro.
"""

# standard library imports
import unittest

# third party imports
import numpy as np

# local imports (problem definition)
from probeye.definition.inference_problem import InferenceProblem
from probeye.definition.forward_model import ForwardModelBase
from probeye.definition.sensor import Sensor
from probeye.definition.noise_model import NormalNoiseModel

# local imports (testing related)
from tests.integration_tests.subroutines import run_inference_engines


class TestProblem(unittest.TestCase):

    def test_multiple_sensors(self, n_steps=200, n_initial_steps=100,
                              n_walkers=20, plot=False, verbose=False,
                              run_emcee=True, run_torch=True):
        """
        Integration test for the problem described at the top of this file.

        Parameters
        ----------
        n_steps : int, optional
            Number of steps (samples) to run. Note that the default number is
            rather low just so the test does not take too long.
        n_walkers : int, optional
            Number of walkers used by the estimator.
        plot : bool, optional
            If True, the data and the posterior distributions are plotted. This
            is deactivated by default, so that the test does not stop until the
            generated plots are closed.
        verbose : bool, optional
            If True, additional information will be printed to the console.
        run_emcee : bool, optional
            If True, the problem is solved with the emcee solver. Otherwise,
            the emcee solver will not be used.
        run_torch : bool, optional
            If True, the problem is solved with the pyro/torch_ solver.
            Otherwise, the pyro/torch_ solver will not be used.
        """

        # ==================================================================== #
        #                          Set numeric values                          #
        # ==================================================================== #

        # 'true' value of A, and its normal prior parameters
        A_true = 1.3
        loc_A = 1.0
        scale_A = 1.0

        # 'true' value of B, and its normal prior parameters
        B_true = -1.0
        loc_B = -2.0
        scale_B = 1.5

        # 'true' value of sd_S1, and its uniform prior parameters
        sd_S1_true = 0.2
        low_S1 = 0.1
        high_S1 = 0.7

        # 'true' value of sd_S2, and its uniform prior parameters
        sd_S2_true = 0.4
        low_S2 = 0.1
        high_S2 = 0.7

        # 'true' value of sd_S3, and its uniform prior parameters
        sd_S3_true = 0.6
        low_S3 = 0.1
        high_S3 = 0.7

        # define sensor positions
        pos_s1 = 0.2
        pos_s2 = 0.5
        pos_s3 = 1.0

        # define global constant; this constant is used here only to test if
        # there are any problems when using global constants
        c = 0.5

        # ==================================================================== #
        #                       Define the Forward Model                       #
        # ==================================================================== #

        class LinearModel(ForwardModelBase):
            def response(self, inp):
                t = inp['time']
                A = inp['A']
                B = inp['B']
                const = inp['const']
                response = dict()
                for os in self.output_sensors:
                    response[os.name] = A * os.x + B * t + const
                return response

        # ==================================================================== #
        #                     Define the Inference Problem                     #
        # ==================================================================== #

        # initialize the inference problem with a useful name
        problem = InferenceProblem("Linear model with three noise models")

        # add all parameters to the problem
        problem.add_parameter('A', 'model',
                              prior=('normal', {'loc': loc_A,
                                                'scale': scale_A}),
                              info="Slope of the graph",
                              tex="$A$")
        problem.add_parameter('B', 'model',
                              prior=('normal', {'loc': loc_B,
                                                'scale': scale_B}),
                              info="Intersection of graph with y-axis",
                              tex='$B$')
        problem.add_parameter('sigma_1', 'noise',
                              prior=('uniform', {'low': low_S1,
                                                 'high': high_S1}),
                              info="Std. dev. of zero-mean noise model for S1",
                              tex=r"$\sigma_1$")
        problem.add_parameter('sigma_2', 'noise',
                              prior=('uniform', {'low': low_S2,
                                                 'high': high_S2}),
                              info="Std. dev. of zero-mean noise model for S1",
                              tex=r"$\sigma_2$")
        problem.add_parameter('sigma_3', 'noise',
                              prior=('uniform', {'low': low_S3,
                                                 'high': high_S3}),
                              info="Std. dev. of zero-mean noise model for S1",
                              tex=r"$\sigma_3$")
        problem.add_parameter('c', 'model', const=c)

        # add the forward model to the problem
        isensor = Sensor("time")
        osensor1 = Sensor("y1", x=pos_s1)
        osensor2 = Sensor("y2", x=pos_s2)
        osensor3 = Sensor("y3", x=pos_s3)
        linear_model = LinearModel(['A', 'B', {'c': 'const'}], [isensor],
                                   [osensor1, osensor2, osensor3])
        problem.add_forward_model("LinearModel", linear_model)

        # add the noise models to the problem
        problem.add_noise_model(NormalNoiseModel(
            prms_def={'sigma_1': 'std'}, sensors=osensor1))
        problem.add_noise_model(NormalNoiseModel(
            prms_def={'sigma_2': 'std'}, sensors=osensor2))
        problem.add_noise_model(NormalNoiseModel(
            prms_def={'sigma_3': 'std'}, sensors=osensor3))

        # ==================================================================== #
        #                Add test data to the Inference Problem                #
        # ==================================================================== #

        # add the experimental data
        np.random.seed(1)
        sd_dict = {osensor1.name: sd_S1_true,
                   osensor2.name: sd_S2_true,
                   osensor3.name: sd_S3_true}

        def generate_data(n_time_steps, n=None):
            time_steps = np.linspace(0, 1, n_time_steps)
            inp = {'A': A_true, 'B': B_true, 'const': c, 'time': time_steps}
            sensors = linear_model(inp)
            for key, val in sensors.items():
                sensors[key] = val + np.random.normal(0.0, sd_dict[key],
                                                      size=n_time_steps)
            sensors[isensor.name] = time_steps
            problem.add_experiment(f'TestSeries_{n}',
                                   sensor_values=sensors,
                                   fwd_model_name='LinearModel')
        for n_exp, n_t in enumerate([101, 51]):
            generate_data(n_t, n=n_exp)

        # give problem overview
        if verbose:
            problem.info()

        # ==================================================================== #
        #                Solve problem with inference engine(s)                #
        # ==================================================================== #

        # this routine is imported from another script because it it used by all
        # integration tests in the same way
        true_values = {'A': A_true, 'B': B_true, 'sigma_1': sd_S1_true,
                       'sigma_2': sd_S2_true, 'sigma_3': sd_S3_true}
        run_inference_engines(problem, true_values=true_values, n_steps=n_steps,
                              n_initial_steps=n_initial_steps,
                              n_walkers=n_walkers, plot=plot, verbose=verbose,
                              run_emcee=run_emcee, run_torch=run_torch)

if __name__ == "__main__":
    unittest.main()