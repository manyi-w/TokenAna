import json
import numpy as np
import os
import sys

from extract_function_calls import process_directory, calculate_cost, calculate_gemini_cost

def get_model_stats(model_name, log_dir, result_file):
    instance_function_calls, completion_tokens, prompt_tokens = process_directory(log_dir, verbose=False)

    # Calculate solve rate
    try:
        with open(result_file, 'r') as f:
            result_data = json.load(f)
        resolved_ids = set(result_data['resolved_ids'])
        
        # Get instance ids from the log files processed
        instance_ids_in_logs = {i[0] for i in instance_function_calls}
        
        # A more robust way to get total instances is to count the latest log files
        latest_files = {}
        if os.path.isdir(log_dir):
            for filename in os.listdir(log_dir):
                if os.path.isfile(os.path.join(log_dir, filename)) and filename.endswith('.txt'):
                    parts = filename[:-4].rsplit('_', 1)
                    if len(parts) == 2:
                        instance_id, try_num_str = parts
                        try_num = int(try_num_str)
                        if instance_id not in latest_files or try_num > latest_files[instance_id][0]:
                            latest_files[instance_id] = (try_num, filename)
        total_instances = len(latest_files)

        if total_instances > 0:
            solve_rate = len(resolved_ids) / total_instances
        else:
            solve_rate = 0
            
        num_empty = len(set(result_data['empty_patch_ids']))
        # num_empty = total_instances - len(instance_ids_in_logs)

    except (FileNotFoundError, json.JSONDecodeError):
        solve_rate = 0
        num_empty = 0

    all_calls = [calls for _, calls in instance_function_calls]
    total_turns = sum(all_calls)

    model_total_prompt = sum(prompt_tokens)
    model_total_completion = sum(completion_tokens)

    cost = 0
    # The cost calculation for gemini needs the parent directory of the log dir
    # cost_log_dir = os.path.dirname(log_dir) if 'gemini' in model_name else log_dir
    cost_log_dir =  log_dir
    if 'gemini' in model_name:
        cost = calculate_gemini_cost(cost_log_dir, verbose=False)
    else:
        cost = calculate_cost(model_name, model_total_prompt, model_total_completion)

    return {
        'solve_rate': solve_rate,
        'empty': num_empty,
        'total_turns': total_turns,
        'input_token': model_total_prompt,
        'output_token': model_total_completion,
        'cost': cost
    }

def generate_table_3():
    models = ['claude', 'gemini', 'gpt']
    percentiles = ['25', '50', '75']
    
    # Base paths
    base_paths = {
        'claude': './experiments',
        'gemini': './experiments',
        'gpt': './experiments'
    }

    # --- Baseline Data ---
    baseline_stats = {}
    for model in models:
        log_dir = os.path.join(base_paths[model], f"{model}_base/log")
        result_file = os.path.join(base_paths[model], f"{model}_base/result.json")
        baseline_stats[model] = get_model_stats(model, log_dir, result_file)

    # --- Percentile Data ---
    percentile_stats = {p: {} for p in percentiles}
    for p in percentiles:
        for model in models:
            log_dir = os.path.join(base_paths[model], f"{model}_{p}/log")
            result_file = os.path.join(base_paths[model], f"{model}_{p}/result.json")
            percentile_stats[p][model] = get_model_stats(model, log_dir, result_file)

    # --- Generate Markdown Table ---
    header = "| LLM     | Solve Rate      | #Empty | #Total Turns | #Input Token | #Output Token | Total Cost          |"
    separator = "|---------|-----------------|---------|---------------|---------------|----------------|---------------------|"
    print(header)
    print(separator)

    # Print baseline rows
    for model in models:
        stats = baseline_stats[model]
        sr_str = f"{stats['solve_rate']:.0%}"
        total_turns_str = f"{stats['total_turns']:,}"
        input_token_str = f"{stats['input_token']:,}"
        output_token_str = f"{stats['output_token']:,}"
        cost_str_baseline = f"${stats['cost']:.2f}"
        print(f"| \\{model:<8}| {sr_str:<15} | {stats['empty']:<7} | {total_turns_str:<13} | {input_token_str:<13} | {output_token_str:<14} | {cost_str_baseline:<20}|")

    # Print percentile rows
    for p in percentiles:
        for model in models:
            stats = percentile_stats[p][model]
            base_stats = baseline_stats[model]
            
            sr_change = ((stats['solve_rate'] - base_stats['solve_rate']) / base_stats['solve_rate'] * 100) if base_stats['solve_rate'] else 0
            cost_change = ((stats['cost'] - base_stats['cost']) / base_stats['cost'] * 100) if base_stats['cost'] else 0

            sr_str = f"{stats['solve_rate']:.0%} ({sr_change:+.2f}%) "
            cost_str = f"${stats['cost']:.2f} ({cost_change:+.2f}%) "
            total_turns_str = f"{stats['total_turns']:,}"
            input_token_str = f"{stats['input_token']:,}"
            output_token_str = f"{stats['output_token']:,}"

            print(f"| \\{model:<8}| {sr_str:<15} | {stats['empty']:<7} | {total_turns_str:<13} | {input_token_str:<13} | {output_token_str:<14} | {cost_str:<20}|")

if __name__ == '__main__':
    generate_table_3()