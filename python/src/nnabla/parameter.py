# Copyright (c) 2017 Sony Corporation. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from contextlib import contextmanager
from collections import OrderedDict
import numpy
import os

import nnabla as nn
from nnabla.logger import logger
import nnabla.utils.nnabla_pb2 as nnabla_pb2

current_scope = OrderedDict()
root_scope = current_scope


@contextmanager
def parameter_scope(name):
    """
    Grouping parameters registered by parametric functions
    listed in :mod:`nnabla.parametric_functions`.

    Example:

    .. code-block:: python

        import nnabla as nn
        import nnabla.parametric_functions as PF
        import nnabla.functions as F

        with nn.parameter_scope('conv1'):
            conv_out1 = PF.convolution(x, 32, (5, 5))
            bn_out1 = PF.batch_normalization(conv_out1)
            act_out1 = F.relu(bn_out1)
        with nn.parameter_scope('conv2'):
            conv_out2 = PF.convolution(act_out1, 64, (3, 3))
            bn_out2 = PF.batch_normalization(conv_out2)
            act_out2 = F.relu(bn_out2)

    """
    global current_scope
    prev_scope = current_scope
    tmp = current_scope.get(name, OrderedDict())
    assert isinstance(tmp, dict)
    current_scope[name] = tmp
    current_scope = tmp
    yield
    current_scope = prev_scope


def get_parameter(key):
    names = key.split('/')
    if len(names) > 1:
        with parameter_scope(names[0]):
            return get_parameter('/'.join(names[1:]))
    global current_scope
    param = current_scope.get(key, None)
    if param is not None:
        assert isinstance(param, nn.Variable)
    return param


def set_parameter(key, param):
    names = key.split('/')
    if len(names) > 1:
        with parameter_scope(names[0]):
            return set_parameter('/'.join(names[1:]), param)
    global current_scope
    current_scope[names[0]] = param


def get_parameter_or_create(name, shape, initializer=None, need_grad=True):
    """
    Returns an existing parameter variable with the provided name.
    If a variable with the provided name does not exist,
    a new variable with the provided name is returned.

    Args:

      name(str): The name under the current scope. If it already exists, the name is queried from the
          parameter manager.
      shape (:obj:`tuple` of :obj:`int`): Shape of created parameter. The shape of the specified
          parameter must match with this shape.
      initializer (~nnabla.initializer.BaseInitializer): An initialization function to be applied to the parameter.
      need_grad (bool): The value for `need_grad` .
          The default is True.

    """
    names = name.split('/')
    if len(names) > 1:
        with parameter_scope(names[0]):
            return get_parameter_or_create('/'.join(names[1:]), shape, initializer, need_grad)
    param = get_parameter(names[0])
    if param is None:
        class VariableInfo:
            pass
        info = VariableInfo()
        info.initializer = initializer
        param = nn.Variable(shape, need_grad=need_grad)
        if initializer is not None:
            param.d = initializer(shape=param.shape)
        set_parameter(name, param)
    else:
        assert param.shape == tuple(shape)
        if need_grad != param.need_grad:
            param = param.unlinked()
            param.need_grad = need_grad
    return param


def get_parameters(params=None, path='', grad_only=True):
    """Get parameter Variables under the current parameter scope.

    Args:
        params (dict): Inernal use. User doesn't set it manually.
        path (str): Internal use.  User doesn't set it manually.
        grad_only (bool): Retrieve all parameters under the current scope if
            False, while only parameters with need_grad=True are retrieved
            if True.

    Returns:
        dict: {:obj:`str` : :obj:`~nnabla.Variable`}

    """

    global current_scope
    if params is None:
        params = OrderedDict()
    for k, v in current_scope.iteritems():
        if isinstance(v, dict):
            with parameter_scope(k):
                params = get_parameters(
                    params, '/'.join([path, k]) if path else k, grad_only=grad_only)
        else:
            assert isinstance(v, nn.Variable)
            if not grad_only or v.need_grad:
                params['/'.join([path, k]) if path else k] = v
    return params


def clear_parameters():
    """Clear all parameters in the current scope."""
    global current_scope
    for key in current_scope.keys():
        del current_scope[key]


def load_parameters(path, proto=None):
    """Load parameters from a file with the specified format.

    Args:
      path : path or file object
    """
    _, ext = os.path.splitext(path)
    if proto is None:
        proto = nnabla_pb2.NNablaProtoBuf()
    if ext == '.h5':
        import h5py
        with h5py.File(path, 'r') as hd:
            keys = []

            def _get_keys(name):
                ds = hd[name]
                if not isinstance(ds, h5py.Dataset):
                    # Group
                    return
                # To preserve order of parameters
                keys.append((ds.attrs.get('index', None), name))
            hd.visit(_get_keys)
            for _, key in sorted(keys):
                ds = hd[key]
                var = get_parameter_or_create(key, ds.shape,
                                              need_grad=ds.attrs['need_grad'])
                var.data.cast(ds.dtype)[...] = ds[...]
                parameter = proto.parameter.add()
                parameter.variable_name = key
                parameter.shape.dim.extend(var.shape)
                parameter.data.extend(numpy.array(var.d).flatten().tolist())
                parameter.need_grad = var.need_grad
    elif ext == '.protobuf':
        with open(path, 'rb') as f:
            proto.MergeFromString(f.read())
            for parameter in proto.parameter:
                var = get_parameter_or_create(
                    parameter.variable_name, parameter.shape.dim)
                param = numpy.reshape(parameter.data, parameter.shape.dim)
                var.d = param
                var.need_grad = parameter.need_grad
    logger.info("Parameter load ({}): {}".format(format, path))
    return proto


def save_parameters(path, format='hdf5'):
    """Save all parameters into a file with the specified format.

    Currently hdf5 and protobuf formats are supported.

    Args:
      path : path or file object
    """
    _, ext = os.path.splitext(path)
    params = get_parameters(grad_only=False)
    if ext == '.h5':
        import h5py
        with h5py.File(path, 'w') as hd:
            params = get_parameters(grad_only=False)
            for i, (k, v) in enumerate(params.iteritems()):
                hd[k] = v.d
                hd[k].attrs['need_grad'] = v.need_grad
                # To preserve order of parameters
                hd[k].attrs['index'] = i
    elif ext == '.protobuf':
        proto = nnabla_pb2.NNablaProtoBuf()
        for variable_name, variable in params.items():
            parameter = proto.parameter.add()
            parameter.variable_name = variable_name
            parameter.shape.dim.extend(variable.shape)
            parameter.data.extend(numpy.array(variable.d).flatten().tolist())
            parameter.need_grad = variable.need_grad

        with open(path, "wb") as f:
            f.write(proto.SerializeToString())
    else:
        logger.critical('Only supported hdf5 or protobuf.')
        assert False
    logger.info("Parameter save ({}): {}".format(format, path))
