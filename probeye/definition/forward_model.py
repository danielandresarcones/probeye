# standard library
import copy as cp
import numpy as np

# local imports
from probeye.subroutines import translate_prms_def
from probeye.subroutines import make_list
from probeye.subroutines import len_or_one


class ForwardModelBase:
    """
    This class serves as a base class for any forward model. When you want to
    define a specific forward model, you need to derive your own class from this
    one, and then define the '__call__' method. The latter essentially describes
    the model function mapping the model input to the output.
    """
    def __init__(self, prms_def_, input_sensors, output_sensors):
        """
        Parameters
        ----------
        prms_def_ : str, list, dict
            Contains the model's latent parameter names. The list may only
            contain strings or one-element dictionaries. It could look, for
            example, like [{'a': 'm'}, 'b']. The one-element dictionaries
            account for the possibility to define a local name for a latent
            parameter that is different from the global name. In the example
            above, the latent parameter with the global name 'a' will be
            referred to as 'm' within the model. So, the one-element dicts have
            the meaning {<global name>: <local name>}. String-elements are
            interpreted as having similar local and global names. Note that the
            local-name option will not be required most of the times. The input
            from global to local name can also be provided as a dict. In the
            example above it would look like {'a': 'm', 'b': 'b'}.
        input_sensors : Sensor, list[Sensor]
            Contains sensor-objects structuring the model input.
        output_sensors : Sensor, list[Sensor]
            Contains sensor-objects structuring the model output.
        """

        # convert the given parameter names to a dictionary with global names
        # as keys and local names as values
        self.prms_def, self.prms_dim = translate_prms_def(prms_def_)

        # other attributes
        self.input_sensors = make_list(input_sensors)
        self.output_sensors = make_list(output_sensors)

        # this attributes might be used by inference engines that need a forward
        # model wrapper, which only returns numeric vectors; for reconstructing
        # the response dictionary from the numeric vector, one needs to know the
        # response dictionary's structure; this dictionaries will then contain
        # the same keys as the response method's return dictionary, while the
        # values will be the number of elements contained in the values; for
        # example {'x': np.array([0, 0.1, 0.2]), 'a': 3.7} will have a structure
        # of {'x': 3, 'a': 1}; this attr. is not used by all inference engines
        self.response_structure = dict()

    @property
    def input_sensor_names(self):
        """Provides input_sensor_names attribute."""
        return [sensor.name for sensor in self.input_sensors]

    @property
    def output_sensor_names(self):
        """Provides input_sensor_names attribute."""
        return [sensor.name for sensor in self.output_sensors]

    def response(self, inp):
        """
        Evaluates the model response and provides computed results for all of
        the model's output sensors. This method must be overwritten by the user.

        Parameters
        ----------
        inp : dict
            Contains both the exp. input data and the  model's parameters. The
            keys are the names, and the values are their numeric values.

        Returns
        -------
        dict
            Contains the model response (value) for each output sensor,
            referenced by the output sensor's name (key).
        """
        raise NotImplementedError(
            "Your model does not have a proper 'response'-method yet. You need "
            "to define this method, so you can evaluate your model.")

    def __call__(self, inp):
        """
        Evaluates the forward model either via calling the original or the
        wrapped response method, depending on the current definition of the
        call-method. This method is equivalent to calling self.response as long
        as the call-method was not overwritten. If it was overwritten, however,
        this method (the __call__-method) makes sure, that the returned value
        has the same format as the returned value by the response-method. See
        self.response for a docstring on its parameters/return values.
        """
        res_ori = self.call(inp)
        if type(res_ori) is dict:
            # in this case it is assumed that res_ori is the dictionary returned
            # by the response-method
            res = res_ori
        else:
            # in this case, the returned value is assumed to be a numeric vector
            # which needs to be translated back to the dictionary structure
            res = self.response_structure
            i = 0
            for key in self.response_structure.keys():
                n_numbers = self.response_structure[key]
                res[key] = res_ori[i:i + n_numbers]
                i += n_numbers
        return res

    def call(self, inp):
        """
        This function can be used by inference engines to wrap the forward
        model's response. This can be done by overwriting this method by one
        that might do something before and after calling self.response(inp).
        See self.response for a docstring on its parameters/return values.
        """
        return self.response(inp)

    def jacobian(self, inp):
        """
        Numerically computes the Jacobian matrix of the forward model and
        returns it in form of a dictionary. Note that this method should be
        overwritten, if there is a more efficient way to compute the jacobian,
        for example, when one can compute the Jacobian analytically.

        Parameters
        ----------
        inp : dict
            Contains both the exp. input data and the  model's parameters. The
            keys are the names, and the values are their numeric values.

        Returns
        -------
        jac_dict : dict or numpy.ndarray
            The Jacobian matrix in dict-form: The keys are the names of the
            forward model's output sensors. The values are dictionaries with the
            forward model's input channel's names as keys and the derivatives as
            values. For example, the element jac['y']['a'] would give the
            derivative dy/da, and jac['y'] would give the gradient of the
            forward model's y-computation with respect to theta (i.e., the
            input channels) in a dictionary-format.
        """
        # prepare the dictionary; this structure needs to be external from the
        # main loop below since the filling of the dictionary could only be
        # efficiently done in the format jac_dict[prm_name][os_name] which is
        # less readable; the format created in the implemented way is easier to
        # to read since jac['y']['a'] corresponds to dy/da in jac['y'] is the
        # gradient of y with respect to theta
        jac_dict = {}
        for output_sensor in self.output_sensors:
            os_name = output_sensor.name
            jac_dict[os_name] = {}
            for prm_name in inp.keys():
                jac_dict[os_name][prm_name] = None

        # eps is the machine precision; it is needed to compute the step size of
        # the central difference scheme below; note that this refers to single
        # precision (float32) since the processed arrays might be defined in
        # float32, in which case using the eps of double precision (float64)
        # would not work since the step size would be too small
        eps = np.finfo(np.float32).eps
        for prm_name, prm_value in inp.items():
            inp_left = cp.copy(inp)
            inp_right = cp.copy(inp)
            x = inp[prm_name]
            h = np.sqrt(eps) * x + np.sqrt(eps)
            inp_left[prm_name] = x - h
            inp_right[prm_name] = x + h
            dx = 2 * h
            response_dict_left = self.response(inp_left)
            response_dict_right = self.response(inp_right)
            for output_sensor in self.output_sensors:
                os_name = output_sensor.name
                jac_dict[os_name][prm_name] = (response_dict_right[os_name] -
                                               response_dict_left[os_name]) / dx
        return jac_dict

    def jacobian_dict_to_array(self, inp, jac_dict):
        """
        Converts the Jacobian in dict-format (computed by the above 'jacobian'
        method) into a numpy array. This method is external to the above
        'jacobian' method, so that it is easier for a user to overwrite the it
        (i.e., the 'jacobian' method) without also having to define the
        conversion into an array.

        Parameters
        ----------
        inp : dict
            See docstring of the 'jacobian'-method above.
        jac_dict : dict
            See docstring of the 'jacobian'-method above.

        Returns
        -------
        jac : numpy.ndarray
            Similar structure as the conventional Jacobi matrix with respect to
            the columns and rows (i.e. the rows are the different gradients and
            the columns are derivatives with respect to one fixed parameter).
        """

        # n1 is the number of the forward model's output sensors; n2 is the
        # number of the forward model's input channels, i.e., the number of
        # input sensors and the number of the forward model's parameters;
        # finally, n3 is the maximum number of elements in the n2 input
        # channels; the model's parameters are usually scalars, but the
        # input sensors might be vectors with more than one element
        n1 = len(self.output_sensors)
        n2 = len(inp)
        n3 = max([len_or_one(v) for v in [*inp.values()]])
        jac = np.zeros((n1, n2, n3))
        for i, prm_dict in enumerate([*jac_dict.values()]):
            for j, derivative in enumerate([*prm_dict.values()]):
                jac[i, j, :] = derivative
        return jac