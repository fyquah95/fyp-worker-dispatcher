open Core
open Common

(* Builds a control flow graph of inlining decisions in the entire
 * source tree.
 *)

module RL = Rl

(* TODO(fyq14): Consider descending into declarations and make
 *              a separate call graph for each one of them?
 *
 *              I believe this might make convergence faster as
 *              it gives a more greedy solution.
 *)
module Function_call : sig
  type t =
    { top_level_offset: Call_site.Offset.t;
      (* [call_stack] here Follows the same convention as those in
       * [Data_collector.t] (aka the override type), that is the first
       * element is the T.O.S. The earliest function call is the last in
       * call_stack
       *)
      call_stack: (Closure_id.t * Call_site.Offset.t) list;
      applied:    Closure_id.t;
    }
  [@@deriving sexp, compare]

  include Comparable.S with type t := t

  val t_of_override : Data_collector.V0.t -> t option
end

type node =
  { inline:    RL.S.t;
    no_inline: RL.S.t;
  }

type t = private
  { transitions:    node RL.S.Map.t;
    root:           RL.S.t;
    function_calls: Function_call.t RL.S.Map.t;
    reverse_map:    RL.S.t Function_call.Map.t;
  }
[@@deriving sexp_of]

val t_of_inlining_tree : Inlining_tree.V0.Top_level.t -> t

val overrides_of_pending_trajectory : t -> RL.Pending_trajectory.t -> Data_collector.V0.t list

(* [None] indicates termination *)
val transition : t -> RL.S.t -> RL.A.t -> RL.S.t

val pprint : ?with_legend:unit -> t -> string
