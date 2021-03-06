open Core
open Common

module S = struct
  module T = struct
    type t =
      | State of int
      | Terminal [@@deriving compare, sexp]

    let to_string t = Sexp.to_string ([%sexp_of: t] t)

    let is_terminal = function
      | Terminal -> true
      | State _  -> false

    let pprint = function
      | Terminal -> "TERM"
      | State s -> Int.to_string s
  end

  let make =
    let r = ref (-1) in
    fun () ->
      r := !r + 1;
      T.State !r
  ;;

  let terminal = T.Terminal

  include T
  include Comparable.Make(T)
end

module A = struct
  module T = struct
    type t = Data_collector.V1.Action.t = Inline | Specialise | Apply
    [@@deriving compare, sexp]
  end

  include T
  include Comparable.Make(T)
end

module SA_pair = struct
  module T = struct
    type t = (S.t * A.t) [@@deriving compare, sexp]
  end

  include T

  module Map = Map.Make(T)
end

module Trajectory = struct
  type 'a t =
    { entries: (S.t * A.t) list;
      terminal_state: S.t;
      reward: float;
      metadata: 'a;
    }
  [@@deriving sexp]
end

module Pending_trajectory = struct
  type t = ((S.t * A.t) list * S.t) [@@deriving sexp]
end

type transition = S.t -> A.t -> S.t

module MCTS = struct

  type value =
    { total_reward: float;
      visits:       int;
    }

  type t =
    { num_plays:      int;
      q_values:       value SA_pair.Map.t;
      rollout_policy: S.t -> A.t;
    }

  let rollout_policy t = Staged.stage t.rollout_policy

  let init ~rollout_policy = { num_plays = 0; q_values = SA_pair.Map.empty; rollout_policy }

  (* TODO(fyq14): How can we encode a delta parameter here? *)
  let mk_policy t =
    let num_plays = t.num_plays in
    let estimate_value (v: value) =
      if v.visits = 0 then
        `Unvisited
      else
      let q = v.total_reward /. float_of_int v.visits in
      let num_plays = Float.of_int (num_plays + 1) in
      let visits = Float.of_int v.visits in
      let upper_bound = Float.sqrt (2. *. (Float.log num_plays) /. visits) in
      `Value (q +. upper_bound)
    in
    Staged.stage (fun s ->
        let inline = SA_pair.Map.find t.q_values (s, A.Inline) in
        let no_inline = SA_pair.Map.find t.q_values (s, A.Apply) in
        match inline, no_inline with
        | None   , None   -> None
        | Some _ , None   -> Some A.Apply  (* Expand to unvisited node *)
        | None   , Some _ -> Some A.Inline
        | Some inline, Some no_inline ->
          let inline_value = estimate_value inline in
          let no_inline_value = estimate_value no_inline in

          (* This tedious matching is required in the asynchronous case
           * where expansion doesn't necessarily mean that the node has
           * been backprop-ed -- implying that visits == 0 is possible
           *)
          begin match inline_value, no_inline_value with
          | `Unvisited, `Unvisited ->
            begin
              if Random.bool () then Some A.Inline
              else Some A.Apply
            end
          | `Unvisited, `Value _ -> Some A.Inline
          | `Value _, `Unvisited -> Some A.Apply
          | `Value inline_value, `Value no_inline_value ->
            if inline_value >. no_inline_value then
              Some A.Inline
            else
              Some A.Apply
          end)
  ;;

  let backprop t ~trajectory =
    let { Trajectory. entries = trajectory; terminal_state = terminal; reward } =
      trajectory
    in
    let rec loop trajectory ~acc =
      match trajectory with
      | hd :: tl ->
        begin match SA_pair.Map.find acc hd with
        | None -> acc
        | Some entry ->
          let update_entry { total_reward; visits; } =
            { total_reward = total_reward +. reward; visits = visits + 1; }
          in
          let entry = update_entry entry in
          let updated = SA_pair.Map.update acc hd ~f:(fun _ -> entry) in
          loop tl ~acc:updated
        end
      | [] -> acc
    in
    { t with
      q_values = loop trajectory ~acc:t.q_values;
      num_plays = t.num_plays + 1;
    }
  ;;

  let expand t ~path:original_path =
    let q_values = t.q_values in
    let rec loop path =
      match path with
      | hd :: tl ->
        begin match SA_pair.Map.find q_values hd with
        | None ->
          let key = hd in
          let data = { total_reward = 0.0; visits = 0 } in
          SA_pair.Map.update q_values key ~f:(fun _ -> data)
        | Some _ -> loop tl
        end
      | [] -> q_values
    in
    { t with q_values = loop original_path }
  ;;
end

