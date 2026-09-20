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
        
        latest_files = {}
        if os.path.isdir(log_dir):
            for filename in os.listdir(log_dir):
                if os.path.isfile(os.path.join(log_dir, filename)) and filename.endswith('.txt'):
                    parts = filename[:-4].rsplit('_', 1)
                    if len(parts) == 2:
                        instance_id, try_num_str = parts
                        try:
                            try_num = int(try_num_str)
                            if instance_id not in latest_files or try_num > latest_files[instance_id][0]:
                                latest_files[instance_id] = (try_num, filename)
                        except ValueError:
                            continue # ignore files that do not end with _<number>
        total_instances = len(latest_files)

        if total_instances > 0:
            solve_rate = len(resolved_ids) / total_instances
        else:
            solve_rate = 0
            
        num_empty = len(set(result_data['empty_patch_ids']))

    except (FileNotFoundError, json.JSONDecodeError):
        solve_rate = 0
        num_empty = 0

    all_calls = [calls for _, calls in instance_function_calls]
    total_turns = sum(all_calls)

    model_total_prompt = sum(prompt_tokens)
    model_total_completion = sum(completion_tokens)

    cost = 0
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

def generate_table_4():
    models = ['claude', 'gemini', 'gpt']
    model_names = {
        'claude': 'Claude 4',
        'gemini': 'Gemini 2.5 Pro',
        'gpt': 'GPT 4.1'
    }
    
    base_paths = {
        'claude': './experiments',
        'gemini': './experiments',
        'gpt': './experiments'
    }

    # --- Data Fetching ---
    stats_50 = {}
    stats_25_50_total = {}
    stats_75 = {}
    stats_50_75_total = {}

    for model in models:
        # 50
        log_dir = os.path.join(base_paths[model], f"{model}_50/log")
        result_file = os.path.join(base_paths[model], f"{model}_50/result.json")
        stats_50[model] = get_model_stats(model, log_dir, result_file)

        # 25_50_total
        log_dir = os.path.join(base_paths[model], f"{model}_25_50_total/log")
        result_file = os.path.join(base_paths[model], f"{model}_25_50_total/result.json")
        stats_25_50_total[model] = get_model_stats(model, log_dir, result_file)

        # 75
        log_dir = os.path.join(base_paths[model], f"{model}_75/log")
        result_file = os.path.join(base_paths[model], f"{model}_75/result.json")
        stats_75[model] = get_model_stats(model, log_dir, result_file)

        # 50_75_total
        log_dir = os.path.join(base_paths[model], f"{model}_50_75_total/log")
        result_file = os.path.join(base_paths[model], f"{model}_50_75_total/result.json")
        stats_50_75_total[model] = get_model_stats(model, log_dir, result_file)


    # --- Generate Markdown Table ---
    header = "| LLM            | Solve Rate               | #Empty              | #Total Turns | #Input Token | #Output Token | Total Cost                      |"
    separator = "|----------------|--------------------------|----------------------|---------------|---------------|----------------|---------------------------------|"
    print(header)
    print(separator)

    # Print 50 rows
    for model in models:
        stats = stats_50[model]
        sr_str = f"{stats['solve_rate'] * 100:.0f}"
        total_turns_str = f"{stats['total_turns']:,}"
        input_token_str = f"{stats['input_token']:,}"
        output_token_str = f"{stats['output_token']:,}"
        cost_str = f"${stats['cost']:.2f}"
        print(f"| {model_names[model]:<14} | {sr_str:<24} | {stats['empty']:<20} | {total_turns_str:<13} | {input_token_str:<13} | {output_token_str:<14} | {cost_str:<31}|")

    # Print 25_50_total rows
    for model in models:
        stats = stats_25_50_total[model]
        base_stats = stats_50[model]
        
        sr_change = ((stats['solve_rate'] - base_stats['solve_rate']) / base_stats['solve_rate'] * 100) if base_stats['solve_rate'] else 0
        cost_change = ((stats['cost'] - base_stats['cost']) / base_stats['cost'] * 100) if base_stats['cost'] else 0
        empty_change = stats['empty'] - base_stats['empty']

        sr_str = f"{stats['solve_rate'] * 100:.0f} ({'↑' if sr_change > 0 else '↓'} {abs(sr_change):.2f}%)"
        
        if empty_change > 0:
            empty_change_str = f"(↑ {abs(empty_change)})"
        elif empty_change < 0:
            empty_change_str = f"(↓ {abs(empty_change)})"
        else:
            empty_change_str = "(0)"
        empty_str = f"{stats['empty']} {empty_change_str}"

        cost_str = f"${stats['cost']:.2f} ({'↑' if cost_change > 0 else '↓'} {abs(cost_change):.2f}%)"
        total_turns_str = f"{stats['total_turns']:,}"
        input_token_str = f"{stats['input_token']:,}"
        output_token_str = f"{stats['output_token']:,}"

        print(f"| {model_names[model]:<14} | {sr_str:<24} | {empty_str:<20} | {total_turns_str:<13} | {input_token_str:<13} | {output_token_str:<14} | {cost_str:<31}|")

    # Print 75 rows
    for model in models:
        stats = stats_75[model]
        sr_str = f"{stats['solve_rate'] * 100:.0f}"
        total_turns_str = f"{stats['total_turns']:,}"
        input_token_str = f"{stats['input_token']:,}"
        output_token_str = f"{stats['output_token']:,}"
        cost_str = f"${stats['cost']:.2f}"
        print(f"| {model_names[model]:<14} | {sr_str:<24} | {stats['empty']:<20} | {total_turns_str:<13} | {input_token_str:<13} | {output_token_str:<14} | {cost_str:<31}|")

    # Print 50_75_total rows
    for model in models:
        stats = stats_50_75_total[model]
        base_stats = stats_75[model]
        
        sr_change = ((stats['solve_rate'] - base_stats['solve_rate']) / base_stats['solve_rate'] * 100) if base_stats['solve_rate'] else 0
        cost_change = ((stats['cost'] - base_stats['cost']) / base_stats['cost'] * 100) if base_stats['cost'] else 0
        empty_change = stats['empty'] - base_stats['empty']

        sr_str = f"{stats['solve_rate'] * 100:.0f} ({'↑' if sr_change > 0 else '↓'} {abs(sr_change):.2f}%)"

        if empty_change > 0:
            empty_change_str = f"(↑ {abs(empty_change)})"
        elif empty_change < 0:
            empty_change_str = f"(↓ {abs(empty_change)})"
        else:
            empty_change_str = "(0)"
        empty_str = f"{stats['empty']} {empty_change_str}"

        cost_str = f"${stats['cost']:.2f} ({'↑' if cost_change > 0 else '↓'} {abs(cost_change):.2f}%)"
        total_turns_str = f"{stats['total_turns']:,}"
        input_token_str = f"{stats['input_token']:,}"
        output_token_str = f"{stats['output_token']:,}"

        print(f"| {model_names[model]:<14} | {sr_str:<24} | {empty_str:<20} | {total_turns_str:<13} | {input_token_str:<13} | {output_token_str:<14} | {cost_str:<31}|")

if __name__ == '__main__':
    generate_table_4()