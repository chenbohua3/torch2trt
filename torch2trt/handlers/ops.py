import numpy as np
import tensorrt as trt
import torch
from torch.nn import functional as F

from torch2trt.core import (current_network, has_trt_tensor,
                                         register_node_handler)
from torch2trt.utils import print_inputs


@register_node_handler("aten::view")
def aten_view(inputs, attributes):
    assert len(inputs) == 2
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        shape = inputs[1][1:]
        # trt tensor shape don't include batch axis
        # TODO add batch size check
        if len(shape) == 1:
            shape += [1, 1]
        # elif len(shape) == 2:
        #     shape += [1]
        layer = net.add_shuffle(inputs[0])
        layer.reshape_dims = shape
        output = layer.get_output(0)
        return [output]
    return [inputs[0].view(*inputs[1])]


@register_node_handler("aten::_convolution")
def aten_convolution(inputs, attributes):
    inp, weight, bias = inputs[:3]
    stride, pad, dilation = inputs[3:6]
    transposed, output_padding, groups = inputs[6:9]
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        assert all([e == 0 for e in output_padding
                    ]), "tensor rt don't support out padding"
        if transposed:
            I, O_groups, *ksize = weight.shape
            O = O_groups * groups
        else:
            O, I_groups, *ksize = weight.shape
            I = I_groups * groups
        ndim = len(ksize)
        assert ndim == 2, "tensorrt only support 2d conv"
        # trt weight format: GKCRS: [num_groups, O_groups, I, H, W]
        weight = weight.detach().cpu().numpy()
        if bias is not None:
            bias = bias.detach().cpu().numpy()
        else:
            bias = trt.Weights()
        if transposed:
            conv = net.add_deconvolution(inputs[0], O, tuple(ksize), weight,
                                         bias)
        else:
            conv = net.add_convolution(inputs[0], O, tuple(ksize), weight,
                                       bias)
            conv.dilation = tuple(dilation)
        conv.stride = tuple(stride)
        conv.padding = tuple(pad)
        conv.num_groups = groups
        output = conv.get_output(0)
        return [output]
    ndim = len(inputs[3])
    assert ndim == 2
    if transposed:
        res = F.conv_transpose2d(inp, weight, bias, stride, pad,
                                 output_padding, groups, dilation)
    else:
        res = F.conv2d(inp, weight, bias, stride, pad, dilation, groups)
    return [res]


@register_node_handler("aten::batch_norm")
def aten_batch_norm(inputs, attributes):
    inp, weight, bias, running_mean, running_var = inputs[:5]
    training, momentum, eps = inputs[5:8]
    # assert training is False
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        running_mean = running_mean.detach().cpu().numpy()
        running_var = running_var.detach().cpu().numpy()
        weight = weight.detach().cpu().numpy()
        bias = bias.detach().cpu().numpy()
        shift = (-running_mean / np.sqrt(running_var + eps)) * weight + bias
        scale = weight / np.sqrt(running_var + eps)
        bn = net.add_scale(inp, trt.ScaleMode.CHANNEL, shift, scale,
                           np.ones_like(shift))
        output = bn.get_output(0)
        return [output]
    res = F.batch_norm(inp, running_mean, running_var, weight, bias,
                       bool(training), momentum, eps)
    return [res]


@register_node_handler("aten::relu_")
def aten_relu_(inputs, attributes):
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        act = net.add_activation(inputs[0], trt.ActivationType.RELU)
        output = act.get_output(0)
        return [output]
    return [F.relu_(inputs[0])]


@register_node_handler("aten::relu")
def aten_relu(inputs, attributes):
    net = current_network()
    if net is not None:
        act = net.add_activation(inputs[0], trt.ActivationType.RELU)
        output = act.get_output(0)
        return [output]
    return [F.relu(inputs[0])]


@register_node_handler("aten::max_pool2d")
def aten_max_pool2d(inputs, attributes):
    inp = inputs[0]
    ksize, stride, pad, dilation = inputs[1:5]
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        layer = net.add_pooling(inp, trt.PoolingType.MAX, ksize)
        layer.stride = stride
        layer.padding = pad
        assert all(
            [b == 1 for b in dilation]), "trt pool don't support dilation"
        output = layer.get_output(0)
        return [output]

    res = F.max_pool2d(inp, ksize, stride, pad, dilation)
    return [res]


@register_node_handler("aten::avg_pool2d")
def aten_avg_pool2d(inputs, attributes):
    inp = inputs[0]
    ksize, stride, pad, ceil_mode, count_include_pad = inputs[1:6]
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        layer = net.add_pooling(inp, trt.PoolingType.AVERAGE, ksize)
        layer.stride = stride
        layer.padding = pad
        layer.average_count_excludes_padding = not count_include_pad
        output = layer.get_output(0)
        return [output]
    inp = inputs[0]
    ksize, stride, pad, ceil_mode = inputs[1:5]
    res = F.avg_pool2d(inp, ksize, stride, pad, bool(ceil_mode),
                       count_include_pad)
    return [res]


@register_node_handler("aten::adaptive_avg_pool2d")
def aten_adaptive_avg_pool2d(inputs, attributes):
    inp = inputs[0]
    ksize = inputs[1]
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        inp_shape = inp.shape[1:]
        ksize = [i // k for i, k in zip(inp_shape, ksize)]
        assert all([i % k == 0 for i, k in zip(inp_shape, ksize)])
        layer = net.add_pooling(inp, trt.PoolingType.AVERAGE, ksize)
        # print("WARNING: adaptive_avg_pool2d support is imcomplete")
        output = layer.get_output(0)
        return [output]
    inp = inputs[0]
    ksize = inputs[1]
    res = F.adaptive_avg_pool2d(inp, ksize)
    return [res]


@register_node_handler("aten::dropout")
def aten_dropout(inputs, attributes):
    inp = inputs[0]
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        return [inputs[0]]
    rate, training = inputs[1:3]
    res = F.dropout2d(inp, rate, bool(training))
    return [res]


@register_node_handler("aten::addmm")
def aten_addmm(inputs, attributes):
    mat_to_add, mat1, mat2 = inputs[:3]
    beta, alpha = inputs[3:5]
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        assert beta == 1 and alpha == 1
        assert len(mat_to_add.shape) == 1
        inp = mat1
        weight = mat2.t().detach().cpu().numpy()
        bias = mat_to_add.detach().cpu().numpy()
        C = weight.shape[0]
        # use fc to implement this
        layer = net.add_fully_connected(inp, C, weight, bias)
        output = layer.get_output(0)
        return [output]

    res = torch.addmm(beta, mat_to_add, alpha, mat1, mat2)
    return [res]


@register_node_handler("aten::cat")
def aten_cat(inputs, attributes):
    tensors, dim = inputs
    net = current_network()
    if net is not None and has_trt_tensor(tensors):
        assert dim > 0
        layer = net.add_concatenation(tensors)
        layer.axis = dim - 1  # trt don't support batch axis
        output = layer.get_output(0)
        return [output]
    res = torch.cat(tensors, dim=dim)
    return [res]


def _trt_torch_slice(net, inp, dim, start, end, step):
    ndim = len(inp.shape)
    starts = [0] * ndim
    out_shapes = [0] * ndim
    steps = [0] * ndim
    for i in range(ndim):
        starts[i] = 0
        out_shapes[i] = inp.shape[i]
        steps[i] = 1
    starts[dim - 1] = start
    out_shapes[dim - 1] = min(end, inp.shape[dim - 1]) - start
    steps[dim - 1] = step
    layer = net.add_slice(inp, tuple(starts), tuple(out_shapes), tuple(steps))
    output = layer.get_output(0)
    return output


@register_node_handler("aten::slice")
def aten_slice(inputs, attributes):
    inp, dim, start, end, step = inputs
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        if dim == 0:
            if start == 0 and step == 1 and end > 100000000:
                return [inp]
            else:
                assert dim > 0, "tensorrt don't support batch axis operation"
        assert step == 1
        output = _trt_torch_slice(net, inp, dim, start, end, step)
        return [output]
    slice_ = slice(start, end, step)
    slices = [slice(None, None, None) for _ in range(dim + 1)]
    slices[dim] = slice_
    # res = torch.slice(inp, dim, start, end, step)
    return [inp[slices]]


def _trt_squeeze(net, inp, dim):
    assert dim > 0
    assert inp.shape[dim - 1] == 1
    shape = list(inp.shape)
    shape[dim - 1] = None
    shape = list([s for s in shape if s is not None])
    layer = net.add_shuffle(inp)
    layer.reshape_dims = shape
    output = layer.get_output(0)
    return output


def _trt_unsqueeze(net, inp, dim):
    assert dim > 0
    shape = list(inp.shape)
    shape.insert(dim - 1, 1)
    layer = net.add_shuffle(inp)
    layer.reshape_dims = shape
    output = layer.get_output(0)
    return output


@register_node_handler("aten::unsqueeze")
def aten_unsqueeze(inputs, attributes):
    inp, dim = inputs
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        return [_trt_unsqueeze(net, inp, dim)]
    return [inp.unsqueeze(dim)]


@register_node_handler("aten::select")
def aten_select(inputs, attributes):
    print_inputs(inputs)
    inp, dim, index = inputs
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        assert dim > 0
        output = _trt_torch_slice(net, inp, dim, index, index + 1, 1)
        output = _trt_squeeze(net, output, dim)
        return [output]
    slice_ = slice(index, index + 1, 1)
    slices = [slice(None, None, None) for _ in range(dim + 1)]
    slices[dim] = slice_
    return [inp[slices].squeeze(dim)]


def _scale_or_elementwise(net, lfs, rfs, op):
    """pytorch elementwise may contains constants.
    if contains constant, use add_scale, otherwise use add_elementwise
    """
    trt_op = {
        "add": trt.ElementWiseOperation.SUM,
        "sub": trt.ElementWiseOperation.SUB,
        "mul": trt.ElementWiseOperation.PROD,
        "div": trt.ElementWiseOperation.DIV,
    }
    assert op in trt_op
    assert not all([isinstance(t, torch.Tensor) for t in [lfs, rfs]])
    if all([isinstance(t, trt.ITensor) for t in [lfs, rfs]]):
        layer = net.add_elementwise(lfs, rfs, trt_op[op])
        output = layer.get_output(0)
        return output
    if isinstance(rfs, torch.Tensor):
        val = rfs.detach().cpu().numpy()
        main = lfs
        scale = val
        if val.size == 1:
            # use scale implementation
            if op == "add":
                shift = trt.Weights(np.array(scale, dtype=np.float32))
                scale = trt.Weights(np.array(1, dtype=np.float32))
            elif op == "sub":
                shift = trt.Weights(np.array(-scale, dtype=np.float32))
                scale = trt.Weights(np.array(1, dtype=np.float32))
            elif op == "mul":
                shift = trt.Weights(np.array(0, dtype=np.float32))
                scale = trt.Weights(np.array(scale, dtype=np.float32))
            elif op == "div":
                shift = trt.Weights(np.array(0, dtype=np.float32))
                scale = trt.Weights(np.array(1 / scale, dtype=np.float32))
            else:
                raise NotImplementedError
            power = trt.Weights(np.array(1, dtype=np.float32))
            layer = net.add_scale(main, trt.ScaleMode.UNIFORM, shift, scale,
                                  power)
        else:
            lfs, rfs = try_convert_to_constant(net, [lfs, rfs])
            layer = net.add_elementwise(lfs, rfs, trt_op[op])
    else:
        lfs, rfs = try_convert_to_constant(net, [lfs, rfs])
        layer = net.add_elementwise(lfs, rfs, trt_op[op])
    output = layer.get_output(0)
    return output


def try_convert_to_constant(net, inputs):
    res = []
    ref_shape = None
    for inp in inputs:
        if isinstance(inp, trt.ITensor):
            ref_shape = inp.shape
    for inp in inputs:
        if isinstance(inp, torch.Tensor):
            inp = inp.detach().cpu().numpy()
            if inp.dtype == np.float64:
                inp = inp.astype(np.float32)
            if len(inp.shape) == 0:
                inp = inp.reshape(*([1] * len(ref_shape)))
            layer = net.add_constant(inp.shape, trt.Weights(inp))
            inp = layer.get_output(0)
        res.append(inp)
    return res


@register_node_handler("aten::mul")
def aten_mul(inputs, attributes):
    # print_inputs(inputs)
    lfs, rfs = inputs
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        output = _scale_or_elementwise(net, lfs, rfs, "mul")
        return [output]
    return [lfs * rfs]


@register_node_handler("aten::mul_")
def aten_mul_(inputs, attributes):
    # print_inputs(inputs)
    lfs, rfs = inputs
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        output = _scale_or_elementwise(net, lfs, rfs, "mul")
        return [output]
    lfs *= rfs
    return [lfs]


@register_node_handler("aten::add_")
def aten_add_(inputs, attributes):
    lfs, rfs, alpha = inputs
    net = current_network()
    assert alpha == 1
    if net is not None and has_trt_tensor(inputs):
        output = _scale_or_elementwise(net, lfs, rfs, "add")
        return [output]
    lfs.add_(rfs)
    return [lfs]


@register_node_handler("aten::add")
def aten_add(inputs, attributes):
    lfs, rfs, alpha = inputs
    assert alpha == 1
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        output = _scale_or_elementwise(net, lfs, rfs, "add")
        return [output]
    return [lfs + rfs]


@register_node_handler("aten::div")
def aten_div(inputs, attributes):
    # print_inputs(inputs)
    lfs, rfs = inputs
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        output = _scale_or_elementwise(net, lfs, rfs, "div")
        return [output]
    return [lfs / rfs]


@register_node_handler("aten::sub")
def aten_sub(inputs, attributes):
    # print_inputs(inputs)
    lfs, rfs, alpha = inputs
    assert alpha == 1
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        output = _scale_or_elementwise(net, lfs, rfs, "sub")
        return [output]
    return [lfs - rfs]


def _axes_to_trt_axis(axes, ndim):
    bit = np.array(1, dtype=np.uint32)
    res = np.array(0, dtype=np.uint32)
    for ax in axes:
        assert ax > 0
        res = np.bitwise_or(res, bit << (ax - 1))
    return int(res)


@register_node_handler("aten::sum")
def aten_sum(inputs, attributes):
    inp, axes, keepdim = inputs
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        axis_trt = _axes_to_trt_axis(axes, len(inp.shape))
        layer = net.add_reduce(inp, trt.ReduceOperation.SUM, axis_trt,
                               bool(keepdim))
        output = layer.get_output(0)
        return [output]
    return [inp.sum(tuple(axes), keepdim=bool(keepdim))]


@register_node_handler("aten::max")
def aten_max(inputs, attributes):
    inp, dim, keepdim = inputs
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        axis_trt = _axes_to_trt_axis([dim], len(inp.shape))
        layer = net.add_reduce(inp, trt.ReduceOperation.MAX, axis_trt,
                               bool(keepdim))
        output = layer.get_output(0)
        return [output, None]
    return [*inp.max(dim, keepdim=bool(keepdim))]


@register_node_handler("aten::permute")
def aten_permute(inputs, attributes):
    inp, params = inputs
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        perm_params = params[1:]
        assert all([p > 0 for p in perm_params])
        layer = net.add_shuffle(inp)
        layer.first_transpose = tuple(p - 1 for p in perm_params)
        output = layer.get_output(0)
        return [output]
    return [inputs[0].permute(*params)]


@register_node_handler("aten::contiguous")
def aten_contiguous(inputs, attributes):
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        return [inputs[0]]
    return [inputs[0].contiguous()]


@register_node_handler("aten::constant_pad_nd")
def aten_constant_pad_nd(inputs, attributes):
    inp, pad_params, val = inputs
    net = current_network()
    if net is not None and has_trt_tensor(inputs):
        assert val == 0.0
        w0, h0, w1, h1 = pad_params
        layer = net.add_padding(inp, (w0, h0), (w1, h1))
        output = layer.get_output(0)
        return [output]
    return [F.pad(inp, pad_params, value=val)]