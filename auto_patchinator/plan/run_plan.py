"""Ties parsing + dependency order + wave mapping + inventory + action sequences together
into the fully resolved, human-reviewable plan the CLI prints before doing anything.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from auto_patchinator.actions.sequences import (
    HOST_OVERRIDES,
    NodeRole,
    captain_revert_dynamic,
    captain_transfer_static,
    get_role_sequences,
    manual_todo,
)
from auto_patchinator.actions.types import Action
from auto_patchinator.config.inventory import Inventory
from auto_patchinator.plan.action_mapping import ActionVerb
from auto_patchinator.plan.dependency import OrderedStep
from auto_patchinator.plan.wave_mapping import hosts_for_groups


@dataclass(frozen=True)
class RunStepPlan:
    excel_step: int
    verb: ActionVerb
    label: str
    groups: tuple[int, ...]
    hostnames: tuple[str, ...]
    external_dependencies: tuple[int, ...]
    pre_group_actions: tuple[Action, ...]
    per_host_actions: dict[str, tuple[Action, ...]]
    post_group_actions: tuple[Action, ...]


def build_run_plan(
    ordered_steps: list[OrderedStep],
    wave_mapping: dict[int, tuple[str, ...]],
    inventory: Inventory,
) -> list[RunStepPlan]:
    plans: list[RunStepPlan] = []
    for ordered in ordered_steps:
        team_step = ordered.team_step
        hostnames = hosts_for_groups(team_step.groups, wave_mapping)

        pre_actions: list[Action] = []
        post_actions: list[Action] = []
        per_host_actions: dict[str, tuple[Action, ...]] = {}
        seen_keys: set[tuple[str, object]] = set()

        for hostname in hostnames:
            host = inventory.get(hostname)
            sequences = get_role_sequences(hostname, host.role)

            if team_step.verb == ActionVerb.STOP:
                per_host_actions[hostname] = sequences.stop_per_node
                group_pre, group_post = sequences.stop_pre_group, sequences.stop_post_group
            else:
                per_host_actions[hostname] = sequences.start_per_node
                group_pre, group_post = sequences.start_pre_group, sequences.start_post_group

            # Dedup pre/post-group actions per distinct role (or per specific overridden
            # host) - not by deep-equality of the generated sequence.
            dedup_key = ("override", hostname) if hostname in HOST_OVERRIDES else ("role", host.role)
            if dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                pre_actions.extend(group_pre)
                post_actions.extend(group_post)

        if ordered.external_dependencies:
            deps = ", ".join(str(d) for d in ordered.external_dependencies)
            pre_actions.insert(
                0,
                manual_todo(
                    "confirm_external_dependencies",
                    f"Confirm step(s) {deps} (owned by another team) have completed.",
                ),
            )

        plans.append(
            RunStepPlan(
                excel_step=team_step.step,
                verb=team_step.verb,
                label=team_step.label,
                groups=team_step.groups,
                hostnames=hostnames,
                external_dependencies=ordered.external_dependencies,
                pre_group_actions=tuple(pre_actions),
                per_host_actions=per_host_actions,
                post_group_actions=tuple(post_actions),
            )
        )

    plans = _inject_captain_actions(plans, inventory)
    return _append_send_mail(plans)


def _append_send_mail(plans: list[RunStepPlan]) -> list[RunStepPlan]:
    """Append a manual send_mail action as the very last post-group action of every step."""
    result = []
    for p in plans:
        send_mail = manual_todo(
            "send_mail",
            f"Send completion e-mail for step {p.excel_step} ({p.label}).",
        )
        result.append(dataclasses.replace(
            p,
            post_group_actions=p.post_group_actions + (send_mail,),
        ))
    return result


def _inject_captain_actions(
    plans: list[RunStepPlan],
    inventory: Inventory,
) -> list[RunStepPlan]:
    """Inject captain transfer once before the first stretched-SH stop in the wave,
    and captain revert once after the last stretched-SH start."""
    if not inventory.stretched_sh_sites:
        return plans

    def _has_stretched_sh(plan: RunStepPlan) -> bool:
        return any(
            h in inventory.hosts and inventory.hosts[h].role == NodeRole.SEARCH_HEAD_STRETCHED
            for h in plan.hostnames
        )

    def _other_site(plan: RunStepPlan) -> str:
        for h in plan.hostnames:
            host = inventory.hosts.get(h)
            if host and host.role == NodeRole.SEARCH_HEAD_STRETCHED:
                return inventory.other_site(host.site)
        raise RuntimeError("_other_site called but no stretched SH host found in plan")

    indexed = list(enumerate(plans))

    first_stop_idx = next(
        (i for i, p in indexed if p.verb == ActionVerb.STOP and _has_stretched_sh(p)),
        None,
    )
    last_start_idx = next(
        (i for i, p in reversed(indexed) if p.verb == ActionVerb.START and _has_stretched_sh(p)),
        None,
    )

    result = list(plans)
    if first_stop_idx is not None:
        p = result[first_stop_idx]
        other_site = _other_site(p)
        captain_host = inventory.captain_candidate(other_site)
        result[first_stop_idx] = dataclasses.replace(
            p,
            pre_group_actions=(captain_transfer_static(other_site, captain_host),) + p.pre_group_actions,
        )
    if last_start_idx is not None:
        p = result[last_start_idx]
        captain_host = inventory.captain_candidate(_other_site(p))
        all_hosts = inventory.stretched_sh_hostnames()
        result[last_start_idx] = dataclasses.replace(
            p,
            post_group_actions=p.post_group_actions + (captain_revert_dynamic(captain_host, all_hosts),),
        )
    return result
