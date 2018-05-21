import argparse
import collections
import StringIO

import tensorflow as tf
from tensorflow.core.framework import graph_pb2

parser = argparse.ArgumentParser(description="codegen")
parser.add_argument("graph", type=str, help="pb file for TF graph")
parser.add_argument("--output", type=str, help="output of this")

_SYM = -1


def gensym():
    global _SYM
    _SYM += 1
    return "v%d" % _SYM


def float_array_from_tensor(tensor, name):
    assert len(tensor.tensor_shape.dim) == 0
    return "Tf_lib.of_float " + str(tensor.float_val[0])


def int_array_from_tensor(tensor, name):
    assert len(tensor.tensor_shape.dim) == 0
    return "Tf_lib.of_int " + str(tensor.int_val[0])


def typed_array_from_tensor(tensor, name):
    from tensorflow.core.framework import types_pb2
    t = types_pb2
    dtype = tensor.dtype
    dispatch_table = {
            t.DT_FLOAT: float_array_from_tensor,
            t.DT_INT32: int_array_from_tensor
    }
    return dispatch_table[dtype](tensor, name)


def shape_to_tuple(s):
    dims = []
    for d in s.dim:
        dims.append(d.size)
    return tuple(dims)

def to_var_name(s):
    return s.replace("-", "_").lower().split(":")[0]


Argument = collections.namedtuple("Argument", ["name", "shape"])


class Codegen(object):

    def __init__(self, nodes):
        self._nodes = nodes
        self._body = []
        self._args = []
        self._generated = {}

    def body(self):
        return self._body

    def output(self):
        f = StringIO.StringIO()
        args = []
        docs = []
        for arg in self._args:
            args.append(arg.name)
            docs.append(
                    "%s - (%s)" %
                    (arg.name, ", ".join(str(x) for x in arg.shape))
            )

        f.write("(*\n")
        for d in docs:
            f.write("  "  + d + "\n")
        f.write("*)\n")

        f.write("let model %s =\n" % " ".join(args))

        for line in self._body:
            f.write("  " + line + "\n")
        f.write(";;")

        return f.getvalue()

    def traverse(self, raw_node_name):
        if raw_node_name in self._generated:
            return self._generated[raw_node_name]
        ret = self.traverse_impl(raw_node_name)
        self._generated[raw_node_name] = ret
        return ret

    def traverse_impl(self, raw_node_name):
        name = to_var_name(raw_node_name)

        if raw_node_name[0] == "^":
            ret = self.traverse(str(raw_node_name[1:]))
            self._body.append("let %s = Tf_lib.bool_not %s in" % (name, ret))
            return name

        node = self._nodes[raw_node_name.split(":")[0]]

        dep_names = []
        for input_node_name in node.input:
            dep_names.append(self.traverse(input_node_name))
        op_name = node.op

        # Regular Ops
        if node.op == "Variable":
            dims = node.attr["shape"].shape
            dims = [str(d.size) for d in dims.dim]
            dims = "; ".join(dims)
            self._body.append(
                    "let %s = Tf_lib.get_variable \"%s\" [| %s |] in"
                    % (name, node.name, dims))

        elif node.op == "Const":
            c = typed_array_from_tensor(node.attr["value"].tensor, name=name)
            self._body.append("let %s = %s in" % (name, c))

        elif node.op == "Placeholder":
            shape = shape_to_tuple(node.attr["shape"].shape)
            self._args.append(Argument(name=name, shape=shape))

        else:
            assert len(dep_names) > 0
            fnc_name = "Tf_lib." + op_name.lower()
            self._body.append("let %s = %s %s in" % (name, fnc_name, " ".join(dep_names)))
        return name

    def run(self, entry_point_name):
        self.traverse(entry_point_name)
        self._body.append(to_var_name(entry_point_name))


def main():
    args = parser.parse_args()
    graph_def = tf.GraphDef()
    with open(args.graph, "rb") as f:
        graph_def.ParseFromString(f.read())
    d = {}
    entry_point = None
    for node in graph_def.node:
        d[node.name] = node
        if node.name.startswith("network_output"):
            if entry_point is None:
                entry_point = node
            else:
                assert False
    codegen = Codegen(d)
    codegen.run(entry_point.name)
    print codegen.output()


if __name__ == "__main__":
    main()
