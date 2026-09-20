import json
import numpy as np
import os
import sys

from extract_function_calls import process_directory, calculate_cost, calculate_gemini_cost

def generate_report():
    log_dirs = {
        'claude': './experiments/claude_base/log',
        'gemini': './experiments/gemini_base/log',
        'gpt': './experiments/gpt_base/log',
    }

    result_files = {
        'claude': './experiments/claude_base/result.json',
        'gemini': './experiments/gemini_base/result.json',
        'gpt': './experiments/gpt_base/result.json',
    }

    model_data = []

    for model_name, log_dir in log_dirs.items():
        instance_function_calls, completion_tokens, prompt_tokens = process_directory(log_dir, verbose=False)

        # Calculate solve rate
        try:
            with open(result_files[model_name], 'r') as f:
                result_data = json.load(f)
            resolved_ids = set(result_data['resolved_ids'])
            total_instances = len(set([i[0] for i in instance_function_calls]))
            if total_instances > 0:
                solve_rate = len(resolved_ids) / total_instances
            else:
                solve_rate = 0
        except (FileNotFoundError, json.JSONDecodeError):
            solve_rate = 0

        all_calls = [calls for _, calls in instance_function_calls]
        total_turns = sum(all_calls)
        
        # Calculate empty runs
        # This is a simplification. A more robust way would be to count instances that failed to produce logs.
        # Based on the provided script, we count instances that didn't have function calls in their last try.
        latest_files = {}
        for filename in os.listdir(log_dir):
            if os.path.isfile(os.path.join(log_dir, filename)) and filename.endswith('.txt'):
                parts = filename[:-4].rsplit('_', 1)
                if len(parts) == 2:
                    instance_id, try_num_str = parts
                    try_num = int(try_num_str)
                    if instance_id not in latest_files or try_num > latest_files[instance_id][0]:
                        latest_files[instance_id] = (try_num, filename)
        
        num_empty = len(latest_files) - len(all_calls)

        p25 = np.percentile(all_calls, 25) if all_calls else 0
        p50 = np.percentile(all_calls, 50) if all_calls else 0
        p75 = np.percentile(all_calls, 75) if all_calls else 0

        model_total_prompt = sum(prompt_tokens)
        model_total_completion = sum(completion_tokens)

        if model_name == 'gemini':
            cost = calculate_gemini_cost(log_dir, verbose=False)
        else:
            cost = calculate_cost(model_name, model_total_prompt, model_total_completion)

        model_data.append({
            'name': model_name,
            'solve_rate': f"{solve_rate:.0%}",
            'empty': num_empty,
            'total_turns': f"{total_turns:,}",
            'p25': p25,
            'p50': p50,
            'p75': p75,
            'input_token': f"{model_total_prompt:,}",
            'output_token': f"{model_total_completion:,}",
            'cost': f"${cost:.2f}"
        })

    # Generate Markdown table
    header = "| LLM     | Solve Rate | #Empty | #Total Turns | # Turn (25th) | # Turn (50th) | # Turn (75th) | #Input Token | #Output Token | Total Cost |"
    separator = "|---------|------------|---------|---------------|----------------|----------------|----------------|---------------|----------------|------------|"
    
    rows = [header, separator]
    for data in model_data:
        row = f"| \\{data['name']} | {data['solve_rate']:<10} | {data['empty']:<7} | {data['total_turns']:<13} | {data['p25']:<14.2f} | {data['p50']:<14.2f} | {data['p75']:<14.2f} | {data['input_token']:<13} | {data['output_token']:<14} | {data['cost']:<10} |"
        rows.append(row)

    print("\n".join(rows))

if __name__ == '__main__':
    generate_report()