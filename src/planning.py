"""Read-only experiment planning; never call method.run or agent.run."""

from .config import ExperimentConfig, config_dict
from .loading import load_adapter
from .models import model_options, model_plan
from .capabilities import accounting_plan
from .control import validate_method


def plan_experiment(config: ExperimentConfig) -> dict:
    method = load_adapter(config.method)
    agent = load_adapter(config.agent)
    dataset = load_adapter(config.dataset)
    tasks = dataset.tasks(**config.dataset.options)
    compatibility = model_plan(agent, config.model, config.agent.options)
    options = (config.agent.options if compatibility["status"] == "blocked" else
               model_options(agent, config.model, config.agent.options))
    method_errors = []
    try:
        validate_method(method, agent, config.method.options, options)
        validate_selection = getattr(dataset, "validate_selection", None)
        if callable(validate_selection):
            validate_selection(method, tasks)
    except ValueError as error:
        method_errors.append(str(error))
    return {
        "status": "plan_only",
        "runtime_ready": False,
        "config": config_dict(config),
        "model_compatibility": compatibility,
        "method_compatibility": {"status": "blocked" if method_errors else "configuration_compatible",
                                 "errors": method_errors, "required_capabilities": list(
                                     getattr(method, "required_capabilities", ()))},
        "accounting_compatibility": accounting_plan(method, agent, options, config.model, dataset=dataset),
        "method_version": getattr(method, "version", "legacy-unversioned"),
        "task_count": len(tasks),
        "tasks": [{"instance_id": task.instance_id, "repo": task.repo,
                   "base_commit": task.base_commit} for task in tasks],
        "agent": agent.plan(options),
        "dataset": dataset.plan(config.dataset.options),
        "remaining": ["Provide prepared Linux images containing the selected source-built agent",
                      "Validate the Linux runtime with a fake model service",
                      "Execute the selected dataset's evaluation plan when authorized"],
    }
