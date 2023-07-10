import cobra

from mmtpy_utils import (
    fetch_mbx_constr_list,
    fetch_norm_sample_mbx_data,
    load_model,
    print_logo,
    set_default_bounds,
    solve_mbx_constraints,
)


def optimize_model(
    model_input: cobra.Model or str,
    output_path: str,
    add_1ba: bool = False,
    silent: bool = False,
    return_outputs: bool = False,
    parallel: bool = False,
) -> dict:
    """
    Optimizes a multi-species model of metabolism by maximizing the flux through
    all fecal transporter (UFEt) reactions and minimizing the flux through all
    fecal exchange reactions (IEX). The maximized UFEt fluxes are then used to
    set the bounds of the IEX reactions.

    Currently, this function works with multi-species models generated by
    mgPipe.m from the Microbiome Modeling Toolbox by Heinken et al. (2022).

    Parameters
    ----------
    model_input : str or cobra.Model
        Path to a multi-species model in any COBRApy supported format or a
        COBRApy model loaded into memory.
    output_path : str
        Path to the directory where the output files should be saved.
    add_1ba : bool, optional
        If True, will set diet 1ba bounds to the model before optimizing, by
        default False.
    silent : bool, optional
        If True, will suppress all set_default_bounds() output, by default False.
    return_outputs : bool, optional
        If True, will return the maximized UFEt fluxes, minimized IEX fluxes, and
        the model reaction bounds after minimization of the IEX fluxes, by default
        False.
    parallel : bool, optional
        If True, will only print start and end of workflow, by default False.

    Returns
    -------
    dict
        A dictionary containing the maximized UFEt reaction fluxes.
    dict
        A dictionary containing the minimized IEX reaction fluxes.
    dict
        A dictionary containing the model reaction bounds after minimization of
        the IEX fluxes.

    Raises
    ------
    ValueError
        If the model_input is not a path to a model or a COBRApy model object.
    """
    # Print the logo
    if not parallel:
        print_logo(
            tool="optimize_model",
            tool_description="Optimize a model by minimizing the flux through IEX reactions constrained by\nmaximized UFEt fluxes.",
            version="0.1.0-beta",
        )
    else:
        print(f"\n[STARTED] 'optimize_model' workflow for {model_input}")

    # Load the model
    if isinstance(model_input, cobra.Model):
        model = model_input
    elif isinstance(model_input, str):
        model = load_model(model_input, simple_model_name=True)
    else:
        raise ValueError(
            "The model_input must be a path to a model or a COBRApy model object."
        )

    # Set the default bounds
    set_default_bounds(model, rxn_type="FEX", silent=silent)

    # Add diet 1ba if desired
    if add_1ba:
        for rxn in model.reactions:
            # TODO check if all related reactions are included
            if "Diet_" in rxn.id and rxn.lower_bound != 0:
                if (
                    "dgchol" in rxn.id
                    or "gchola" in rxn.id
                    or "tchola" in rxn.id
                    or "tdchola" in rxn.id
                ):
                    model.reactions.get_by_id(rxn.id).bounds = (-1000.0, 0.0)

    #########################################################
    # Part 1: maximize the flux through all UFEt reactions
    #########################################################
    if not parallel:
        print(f"\n[STARTED] Part 1: maximizing UFEt fluxes for {model.name}")

    # Fetch all UFEt reactions and store them in a list
    UFEt_rxn_list = []
    for rxn in model.reactions:
        if "UFEt_" in rxn.id:
            UFEt_rxn_list.append(rxn.id)

    # Maximize the flux through all UFEt reactions
    counter = 0
    counter_max = len(UFEt_rxn_list)
    maximized_UFEt_flux_list = []
    for rxn in UFEt_rxn_list:
        counter += 1
        if not parallel:
            print(
                f"\n\tMaximizing UFEt reaction {str(counter)} of {str(counter_max)} for {model.name}"
            )
        model.objective = rxn
        solution = model.optimize()
        maximized_UFEt_flux_list.append(solution.objective_value)
        if not parallel:
            print(f"\t\t{rxn}:\t{solution.objective_value}")

    # Create a dictionary of the maximized UFEt fluxes
    maximized_UFEt_flux_dict = dict(zip(UFEt_rxn_list, maximized_UFEt_flux_list))

    if not parallel:
        print(f"\n[DONE] Part 1: maximization complete for {model.name}")

        #########################################################
        # Part 2: minimize the flux through all IEX reactions
        #########################################################

        print(f"\n[STARTED] Part 2: minimizing IEX fluxes for {model.name}")

    # Constrain the UFEt reactions by the maximized UFEt fluxes and minimize the
    # flux through all IEX reactions
    counter = 0
    counter_max = len(UFEt_rxn_list)
    minimized_IEX_flux_dict = dict()
    for i in range(len(UFEt_rxn_list)):
        counter += 1
        if not parallel:
            print(
                f"\n\tMinimizing IEX reaction {str(counter)} of {str(counter_max)} for {model.name}"
            )
        if maximized_UFEt_flux_list[i] != 0.0:
            # Store the old bounds for the UFEt reaction
            saved_bounds = model.reactions.get_by_id(UFEt_rxn_list[i]).bounds

            # Set the bounds for the UFEt reaction to the calculated maximum
            model.reactions.get_by_id(UFEt_rxn_list[i]).bounds = (
                maximized_UFEt_flux_list[i],
                maximized_UFEt_flux_list[i],
            )

            # Rename the UFEt reaction to match the metabolite name
            metabolite = UFEt_rxn_list[i].replace("UFEt_", "") + "[u]"

            # Iterate over all IEX reactions for each metabolite
            for rxn in model.metabolites.get_by_id(metabolite).reactions:
                # If it is an IEX reaction, minimize the reaction flux
                if "IEX" in rxn.id:
                    model.objective = model.reactions.get_by_id(rxn.id)
                    solution = model.optimize(objective_sense="minimize")
                    minimized_IEX_flux_dict[rxn.id] = solution.objective_value
                    if not parallel:
                        print(f"\t\t{rxn.id}:\t{solution.objective_value}")

            # Restore the bounds for the minimized IEX reaction
            model.reactions.get_by_id(UFEt_rxn_list[i]).bounds = saved_bounds
        else:
            if not parallel:
                print("\t\tSkipping reaction because the maximized flux is 0.0")

    # Create a dictionary of the minimized IEX fluxes
    model_rxn_bounds_dict = dict()
    for rxn in model.reactions:
        model_rxn_bounds_dict[rxn.id] = rxn.bounds

    # Write the minimized IEX fluxes to a file
    with open(f"{output_path}/{model.name}_opt_flux.txt", "w") as f:
        for rxn_id in minimized_IEX_flux_dict:
            f.write(f"{rxn_id}:\t{minimized_IEX_flux_dict[rxn_id]}\n")

    if not parallel:
        print(f"\n[DONE] Part 2: minimization complete for {model.name}")
    else:
        print(f"\n[DONE] 'optimize_model' workflow for {model_input}")

    if return_outputs:
        return maximized_UFEt_flux_dict, minimized_IEX_flux_dict, model_rxn_bounds_dict


def optimize_model_mbx(
    model_input: str,
    mbx_path: str,
    output_path: str,
    silent: bool = False,
    verbose: bool = True,
    return_outputs: bool = False,
    parallel: bool = False,
) -> dict:
    """
    Optimize the model for each metabolite in the metabolomics data.

    Parameters
    ----------
    model_input : str or cobra.Model
        Path to the model file or a COBRApy model object.
    mbx_path : str
        Path to the metabolomics data file.
    output_path : str
        Path to the output directory.
    silent : bool
        If True, the function will not print the boundary changes and VMH name matching outputs.
    verbose : bool
        If True, the function will print the zero-valued optimization solution outputs.
    return_outputs : bool
        If True, the function will return the maximized and minimized FEX fluxes.
    parallel : bool
        If True, the function will not print the progress outputs.

    Returns
    -------
    dict
        Dictionary of the optimized solutions for each metabolite.

    Raises
    ------
    ValueError
        If the model_input is not a path to a model or a COBRApy model object.
    """
    # Print the logo
    if not parallel:
        print_logo(
            tool="optimize_model_mbx",
            tool_description="Optimize a model for each metabolite given the MBX data.",
            version="0.1.0-beta",
        )
    else:
        print(f"\n[STARTED] 'optimize_model_mbx' workflow for {model_input}")

    # Load the model
    if isinstance(model_input, cobra.Model):
        model = model_input
    elif isinstance(model_input, str):
        model = load_model(model_input)
    else:
        raise ValueError(
            "The model_input must be a path to a model or a COBRApy model object."
        )

    # Set the solver interface
    model.solver = "gurobi"

    # Get the model-specific metabolomics data
    mbx_metab_norm_dict = fetch_norm_sample_mbx_data(
        model_input=model,
        mbx_filepath=mbx_path,
        match_key_output_filepath=output_path,
        silent=silent,
    )

    # Set all fecal exchange reaction lower bounds to zero
    set_default_bounds(model=model, rxn_type="FEX", silent=silent)

    # Fetch and test the constraint list
    mbx_constraints = fetch_mbx_constr_list(
        model=model, mbx_metab_norm_dict=mbx_metab_norm_dict
    )

    # Fetch the slack constraints if needed
    mbx_constr = solve_mbx_constraints(
        model=model, constraints=mbx_constraints, parallel=parallel
    )

    # Add the constraints to the model
    model.add_cons_vars(mbx_constr)
    model.solver.update()

    # Optimize the model by minimizing the fluxes of reactions for each metabolite
    min_opt_solutions = dict()
    if not parallel:
        print(f"\n[Minimizing the model {model.name} for each metabolite]")
    for rxn_id in [
        rxn.id
        for rxn in model.reactions
        if rxn.id.startswith("EX_") and rxn.id.endswith("[fe]")
    ]:
        model.objective = model.reactions.get_by_id(rxn_id)
        solution = model.optimize(objective_sense="minimize")
        if solution.objective_value != 0.0 or verbose:
            if not parallel:
                print(
                    f"\tMinimized: {model.reactions.get_by_id(rxn_id).id}:\t{solution.objective_value}"
                )
        min_opt_solutions[rxn_id] = solution.objective_value

    # Optimize the model by maximizing the fluxes of reactions for each metabolite
    max_opt_solutions = dict()
    if not parallel:
        print(f"\n[Maximizing the model {model.name} for each metabolite]")
    for rxn_id in [
        rxn.id
        for rxn in model.reactions
        if rxn.id.startswith("EX_") and rxn.id.endswith("[fe]")
    ]:
        model.objective = model.reactions.get_by_id(rxn_id)
        solution = model.optimize(objective_sense="maximize")
        if solution.objective_value != 0.0 or verbose:
            if not parallel:
                print(
                    f"\tMaximized: {model.reactions.get_by_id(rxn_id).id}:\t{solution.objective_value}"
                )
        max_opt_solutions[rxn_id] = solution.objective_value

    # Save the results
    with open(f"{output_path}/{model.name}_min_mbx_opt_flux.txt", "w") as f:
        for rxn_id in min_opt_solutions:
            f.write(f"{rxn_id}:\t{min_opt_solutions[rxn_id]}\n")
    with open(f"{output_path}/{model.name}_max_mbx_opt_flux.txt", "w") as f:
        for rxn_id in max_opt_solutions:
            f.write(f"{rxn_id}:\t{max_opt_solutions[rxn_id]}\n")

    if parallel:
        print(f"\n[DONE] 'optimize_model_mbx' workflow for {model_input}")

    if return_outputs:
        return min_opt_solutions, max_opt_solutions
