import argparse
import collections
import StringIO

import tensorflow as tf
from tensorflow.core.framework import graph_pb2

parser = argparse.ArgumentParser(description="codegen")
parser.add_argument("graph", type=str, help="pb file for TF graph")
parser.add_argument("--weights-module", type=str, help="module name (starts with caps) of weights module to use")

VariableDimCheck = collections.namedtuple("VariableDimCheck", ["var_name", "dims"])

_SYM = -1


def gensym():
    global _SYM
    _SYM += 1
    return "v%d" % _SYM

def print_float(x):
    if x < 0.0:
        return "(-." + str(abs(x)) + ")"
    else:
        return str(abs(x))


def float_array_from_tensor(tensor, name):
    assert len(tensor.tensor_shape.dim) == 0
    return "Tf_lib.of_float " + print_float(tensor.float_val[0])


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
    name = s.replace("-", "_").lower().replace(":", "__")
    if name[0] == '^':
        return str(name[1:])
    else:
        return name

def find_control_input_from_input_names(input_names):
    for name in input_names:
        if name[0] == '^':
            return name
    return None


Argument = collections.namedtuple("Argument", ["name", "shape"])


class Codegen(object):

    def __init__(self, nodes, weights_module):
        self._nodes = nodes
        self._body = []
        self._args = []
        self._generated = {}
        self._sanity_tests = []
        self._weights_module = weights_module

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

        f.write("let feed_forward %s =\n" % " ".join(args))

        for line in self._body:
            f.write("  " + line + "\n")
        f.write(";;\n\n")

        if len(self._sanity_tests) > 0:
            f.write("let () =\n")
            for sanity_test in self._sanity_tests:
                dims = "[|" + "; ".join(sanity_test.dims) + "|]"
                f.write("  assert (Tf_lib.shape %s.%s = %s);\n" % (self._weights_module, sanity_test.var_name, dims))
            f.write("  ()")
            f.write(";;")

        return f.getvalue()

    def traverse(self, raw_node_name):
        if raw_node_name in self._generated:
            return self._generated[raw_node_name]
        ret = self.traverse_impl(raw_node_name)
        self._generated[raw_node_name] = ret
        return ret

    def traverse_impl(self, raw_node_name):
        if raw_node_name[0] == '^':
            return self.traverse(str(raw_node_name[1:]))
        if ":" in raw_node_name:
            return self.traverse(raw_node_name.split(":")[0])

        name = to_var_name(raw_node_name)
        node = self._nodes[raw_node_name.split(":")[0]]

        dep_names = []
        for input_node_name in node.input:
            dep_name = self.traverse(input_node_name)
            output_pose = 0
            if ":" in input_node_name:
                output_pose = int(input_node_name.split(":")[1])

            # Much hacki-ness
            fnc_name = [ "fst", "snd" ]
            if "Switch" in input_node_name:
                dep_name = "(%s %s)" % (fnc_name[output_pose], dep_name)
            dep_names.append(dep_name)
        op_name = node.op

        # Regular Ops
        if node.op == "Identity":
            control_input_name = find_control_input_from_input_names(node.input)
            if control_input_name is None:
                assert len(node.input) == 1
                self._body.append("let %s = %s in" % (name, dep_names[0]))
                return name
            else:
                assert len(node.input) == 2
                (real_input,) = [x for x in node.input if x != control_input_name]
                # (data, pred)
                self._body.append("let %s =" % to_var_name(name))
                self._body.append("  match (%s) with" % dep_names[1])
                self._body.append("  | Tf_lib.Nothing -> Tf_lib.Nothing")
                self._body.append("  | _ -> %s" % dep_names[0])
                self._body.append("in")

        elif node.op == "Variable":
            dims = node.attr["shape"].shape
            dims = [str(d.size) for d in dims.dim]
            self._body.append("let %s = %s.%s in" % (name, self._weights_module, name))
            self._sanity_tests.append(VariableDimCheck(var_name=name, dims=dims))
            # self._body.append(
            #         "let %s = Tf_weights.%s \"%s\" [| %s |] in"
            #         % (name, node.name, dims))

        elif node.op == "Const":
            c = typed_array_from_tensor(node.attr["value"].tensor, name=name)
            self._body.append("let %s = %s in" % (name, c))

        elif node.op == "Switch":
            assert len(dep_names) == 2
            data_name = dep_names[0]
            pred_name = dep_names[1]
            self._body.append(
                    "let %s = if Tf_lib.eval_bool %s then (Tf_lib.Nothing, %s) else (%s, Tf_lib.Nothing) in"
                    % (name, pred_name, data_name, data_name)
            )

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


def run(graph, weights_module):
    graph_def = tf.GraphDef()
    with open(graph, "rb") as f:
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
    codegen = Codegen(d, weights_module=weights_module)
    codegen.run(entry_point.name)
    return codegen.output()


def main():
    args = parser.parse_args()
    print run(graph=args.graph, weights_module=args.weights_module)


if __name__ == "__main__":
    main()
