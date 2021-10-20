"""
Linear regression example where a prior parameter is a latent parameter
--------------------------------------------------------------------------------
The model equation is y = a * x + b with a, b being the model parameters and the
noise model is a normal zero-mean distribution with the std. deviation to infer.
Additionally, the location parameter of a's prior is considered a latent
parameter.The problem is solved via sampling using emcee and pyro.
"""

# standard library imports
import unittest


# third party imports
import numpy as np
import matplotlib.pyplot as plt

# local imports (problem definition)
from probeye.definition.inference_problem import InferenceProblem
from probeye.definition.forward_model import ForwardModelBase
from probeye.definition.sensor import Sensor
from probeye.definition.noise_model import NormalNoiseModel

# local imports (testing related)
from tests.integration_tests.subroutines import run_inference_engines


class TestProblem(unittest.TestCase):

    def test_prior_calibration(self, n_steps=200, n_initial_steps=100,
                               n_walkers=20, plot=False, verbose=False,
                               run_emcee=True, run_torch=True):
        """
        Integration test for the problem described at the top of this file.

        Parameters
        ----------
        n_steps : int, optional
            Number of steps (samples) to run. Note that the default number is
            rather low just so the test does not take too long.
        n_initial_steps : int, optional
            Number of steps for initial (burn-in) sampling.
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

        # 'true' value of a, and its normal prior parameters
        a_true = 2.5
        scale_a = 1.0

        # uniform prior-parameters of 'loc_a'
        low_loc_a = 2.0
        high_loc_a = 3.0

        # 'true' value of b, and its normal prior parameters
        b_true = 1.7
        loc_b = 1.0
        scale_b = 1.0

        # 'true' value of noise sd, and its uniform prior parameters
        sigma_noise = 0.5
        low_sigma = 0.1
        high_sigma = 0.6

        # the number of generated experiment_names and seed for random numbers
        n_tests = 100
        seed = 1

        # ==================================================================== #
        #                       Define the Forward Model                       #
        # ==================================================================== #

        class LinearModel(ForwardModelBase):
            def response(self, inp):
                x = inp['x']
                a = inp['a']
                b = inp['b']
                response = {}
                for os in self.output_sensors:
                    response[os.name] = a * x + b
                return response

        # ==================================================================== #
        #                     Define the Inference Problem                     #
        # ==================================================================== #

        # initialize the inference problem with a useful name
        problem = InferenceProblem(
            "Linear model with normal noise and prior-prior")

        # add all parameters to the problem
        problem.add_parameter('loc_a', 'prior',
                              info="Location parameter of normal prior for 'a'",
                              tex=r"$\mu_a^\mathrm{prior}$",
                              prior=('uniform', {'low': low_loc_a,
                                                 'high': high_loc_a}))
        problem.add_parameter('a', 'model',
                              info="Slope of the graph",
                              tex="$a$",
                              prior=('normal', {'loc': 'loc_a',
                                                'scale': scale_a}))
        problem.add_parameter('b', 'model',
                              info="Intersection of graph with y-axis",
                              tex='$b$',
                              prior=('normal', {'loc': loc_b,
                                                'scale': scale_b}))
        problem.add_parameter('sigma', 'noise',
                              info="Std. dev, of 0-mean noise model",
                              tex=r"$\sigma$",
                              prior=('uniform', {'low': low_sigma,
                                                 'high': high_sigma}))

        # add the forward model to the problem
        isensor = Sensor("x")
        osensor = Sensor("y")
        linear_model = LinearModel(['a', 'b'], [isensor], [osensor])
        problem.add_forward_model("LinearModel", linear_model)

        # add the noise model to the problem
        problem.add_noise_model(NormalNoiseModel(
            prms_def={'sigma': 'std'}, sensors=osensor))

        # ==================================================================== #
        #                Add test data to the Inference Problem                #
        # ==================================================================== #

        # data-generation; normal noise with constant variance around each point
        np.random.seed(seed)
        x_test = np.linspace(0.0, 1.0, n_tests)
        y_true = linear_model(
            {isensor.name: x_test, 'a': a_true, 'b': b_true})[osensor.name]
        y_test = np.random.normal(loc=y_true, scale=sigma_noise)

        # add the experimental data
        problem.add_experiment(f'TestSeries_1', fwd_model_name="LinearModel",
                               sensor_values={isensor.name: x_test,
                                              osensor.name: y_test})

        # give problem overview
        if verbose:
            problem.info()

        # plot the true and noisy data
        if plot:
            plt.scatter(x_test, y_test, label='measured data',
                        s=10, c="red", zorder=10)
            plt.plot(x_test, y_true, label='true', c="black")
            plt.xlabel(isensor.name)
            plt.ylabel(osensor.name)
            plt.legend()
            plt.tight_layout()
            plt.draw()  # does not stop execution

        # ==================================================================== #
        #                Solve problem with inference engine(s)                #
        # ==================================================================== #

        # this routine is imported from another script because it it used by all
        # integration tests in the same way
        true_values = {'loc_a': a_true, 'a': a_true, 'b': b_true,
                       'sigma': sigma_noise}
        run_inference_engines(problem, true_values=true_values, n_steps=n_steps,
                              n_initial_steps=n_initial_steps,
                              n_walkers=n_walkers, plot=plot, verbose=verbose,
                              run_emcee=run_emcee, run_torch=run_torch)

if __name__ == "__main__":
    unittest.main()