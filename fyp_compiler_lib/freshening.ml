(**************************************************************************)
(*                                                                        *)
(*                                 OCaml                                  *)
(*                                                                        *)
(*                       Pierre Chambart, OCamlPro                        *)
(*           Mark Shinwell and Leo White, Jane Street Europe              *)
(*                                                                        *)
(*   Copyright 2013--2016 OCamlPro SAS                                    *)
(*   Copyright 2014--2016 Jane Street Group LLC                           *)
(*                                                                        *)
(*   All rights reserved.  This file is distributed under the terms of    *)
(*   the GNU Lesser General Public License version 2.1, with the          *)
(*   special exception on linking described in the file LICENSE.          *)
(*                                                                        *)
(**************************************************************************)

[@@@ocaml.warning "+a-4-9-30-40-41-42"]

type tbl = {
  sb_var : Variable.t Variable.Map.t;
  sb_mutable_var : Mutable_variable.t Mutable_variable.Map.t;
  sb_exn : Static_exception.t Static_exception.Map.t;
  (* Used to handle substitution sequences: we cannot call the substitution
     recursively because there can be name clashes. *)
  back_var : Variable.t list Variable.Map.t;
  back_mutable_var : Mutable_variable.t list Mutable_variable.Map.t;
}

type t =
  | Inactive
  | Active of tbl

type subst = t

let empty_tbl = {
  sb_var = Variable.Map.empty;
  sb_mutable_var = Mutable_variable.Map.empty;
  sb_exn = Static_exception.Map.empty;
  back_var = Variable.Map.empty;
  back_mutable_var = Mutable_variable.Map.empty;
}

let print ppf = function
  | Inactive -> Format.fprintf ppf "Inactive"
  | Active tbl ->
    Format.fprintf ppf "Active:@ ";
    Variable.Map.iter (fun var1 var2 ->
        Format.fprintf ppf "%a -> %a@ "
          Variable.print var1
          Variable.print var2)
      tbl.sb_var;
    Mutable_variable.Map.iter (fun mut_var1 mut_var2 ->
        Format.fprintf ppf "(mutable) %a -> %a@ "
          Mutable_variable.print mut_var1
          Mutable_variable.print mut_var2)
      tbl.sb_mutable_var;
    Variable.Map.iter (fun var vars ->
        Format.fprintf ppf "%a -> %a@ "
          Variable.print var
          Variable.Set.print (Variable.Set.of_list vars))
      tbl.back_var;
    Mutable_variable.Map.iter (fun mut_var mut_vars ->
        Format.fprintf ppf "(mutable) %a -> %a@ "
          Mutable_variable.print mut_var
          Mutable_variable.Set.print (Mutable_variable.Set.of_list mut_vars))
      tbl.back_mutable_var

let empty = Inactive

let empty_preserving_activation_state = function
  | Inactive -> Inactive
  | Active _ -> Active empty_tbl

let activate = function
  | Inactive -> Active empty_tbl
  | Active _ as t -> t

let rec add_sb_var sb id id' =
  let sb = { sb with sb_var = Variable.Map.add id id' sb.sb_var } in
  let sb =
    try let pre_vars = Variable.Map.find id sb.back_var in
      List.fold_left (fun sb pre_id -> add_sb_var sb pre_id id') sb pre_vars
    with Not_found -> sb in
  let back_var =
    let l = try Variable.Map.find id' sb.back_var with Not_found -> [] in
    Variable.Map.add id' (id :: l) sb.back_var in
  { sb with back_var }

let rec add_sb_mutable_var sb id id' =
  let sb =
    { sb with
      sb_mutable_var = Mutable_variable.Map.add id id' sb.sb_mutable_var;
    }
  in
  let sb =
    try
      let pre_vars = Mutable_variable.Map.find id sb.back_mutable_var in
      List.fold_left (fun sb pre_id -> add_sb_mutable_var sb pre_id id')
        sb pre_vars
    with Not_found -> sb in
  let back_mutable_var =
    let l =
      try Mutable_variable.Map.find id' sb.back_mutable_var
      with Not_found -> []
    in
    Mutable_variable.Map.add id' (id :: l) sb.back_mutable_var
  in
  { sb with back_mutable_var }

let apply_static_exception t i =
  match t with
  | Inactive ->
    i
  | Active t ->
    try Static_exception.Map.find i t.sb_exn
    with Not_found -> i

let add_static_exception t i =
  match t with
  | Inactive -> i, t
  | Active t ->
    let i' = Static_exception.create () in
    let sb_exn =
      Static_exception.Map.add i i' t.sb_exn
    in
    i', Active { t with sb_exn; }

let active_add_variable t id =
  let id' = Variable.rename id in
  let t = add_sb_var t id id' in
  id', t

let active_add_parameter t param =
  let param' = Parameter.rename param in
  let t = add_sb_var t (Parameter.var param) (Parameter.var param') in
  param', t

let add_variable t id =
  match t with
  | Inactive -> id, t
  | Active t ->
     let id', t = active_add_variable t id in
     id', Active t

let active_add_parameters' t (params:Parameter.t list) =
  List.fold_right (fun param (params, t) ->
      let param', t = active_add_parameter t param in
      param' :: params, t)
    params ([], t)

let add_variables t defs =
  List.fold_right (fun (id, data) (defs, t) ->
      let id', t = add_variable t id in
      (id', data) :: defs, t) defs ([], t)

let add_variables' t ids =
  List.fold_right (fun id (ids, t) ->
      let id', t = add_variable t id in
      id' :: ids, t) ids ([], t)

let active_add_mutable_variable t id =
  let id' = Mutable_variable.freshen id in
  let t = add_sb_mutable_var t id id' in
  id', t

let add_mutable_variable t id =
  match t with
  | Inactive -> id, t
  | Active t ->
     let id', t = active_add_mutable_variable t id in
     id', Active t

let active_find_var_exn t id =
  try Variable.Map.find id t.sb_var with
  | Not_found ->
      Misc.fatal_error (Format.asprintf "find_var: can't find %a@."
          Variable.print id)

let apply_variable t var =
  match t with
  | Inactive -> var
  | Active t ->
   try Variable.Map.find var t.sb_var with
   | Not_found -> var

let apply_mutable_variable t mut_var =
  match t with
  | Inactive -> mut_var
  | Active t ->
   try Mutable_variable.Map.find mut_var t.sb_mutable_var with
   | Not_found -> mut_var

let rewrite_recursive_calls_with_symbols t
      (function_declarations : Flambda.function_declarations)
      ~make_closure_symbol =
  match t with
  | Inactive -> function_declarations
  | Active _ ->
    let all_free_symbols =
      Flambda_utils.all_free_symbols function_declarations
    in
    let closure_symbols_used = ref false in
    let closure_symbols =
      Variable.Map.fold (fun var _ map ->
        let closure_id = Closure_id.wrap var in
        let sym = make_closure_symbol closure_id in
        if Symbol.Set.mem sym all_free_symbols then begin
          closure_symbols_used := true;
          Symbol.Map.add sym var map
        end else begin
          map
        end)
      function_declarations.funs Symbol.Map.empty
    in
    if not !closure_symbols_used then begin
      (* Don't waste time rewriting the function declaration(s) if there
         are no occurrences of any of the closure symbols. *)
      function_declarations
    end else begin
      let funs =
        Variable.Map.map (fun (ffun : Flambda.function_declaration) ->
          let body =
            Flambda_iterators.map_toplevel_named
              (* CR-someday pchambart: This may be worth deep substituting
                 below the closures, but that means that we need to take care
                 of functions' free variables. *)
              (function
                | Symbol sym when Symbol.Map.mem sym closure_symbols ->
                  Expr (Var (Symbol.Map.find sym closure_symbols))
                | e -> e)
              ffun.body
          in
          Flambda.update_body_of_function_declaration ffun ~body)
          function_declarations.funs
      in
      Flambda.update_function_declarations function_declarations ~funs
    end

module Project_var = struct
  type t =
    { vars_within_closure : Var_within_closure.t Var_within_closure.Map.t;
      closure_id : Closure_id.t Closure_id.Map.t }

  let empty =
    { vars_within_closure = Var_within_closure.Map.empty;
      closure_id = Closure_id.Map.empty;
    }

  let print ppf t =
    Format.fprintf ppf "{ vars_within_closure %a, closure_id %a }"
      (Var_within_closure.Map.print Var_within_closure.print)
      t.vars_within_closure
      (Closure_id.Map.print Closure_id.print)
      t.closure_id
end
