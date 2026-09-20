import json
import os
import re
import ast
import numpy as np
import math

selected_instances = set()
with open('./swebench_verified_subset.json', 'r') as f:
    data = json.load(f)
    for d in data:
        selected_instances.add(d['instance_id'])


def calculate_gemini_token(log_dir, instance_id, verbose=True):
    output_dir = os.path.join(os.path.dirname(log_dir), 'output')

    filepath = os.path.join(output_dir, "task_" + instance_id + '.log')   
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"fail to read {filepath}: {e}")
        assert False

    parts = content.split("current_try:")
    if len(parts) < 2:
        assert False
        
    
    last_try_content = parts[-1]

    usage_matches = []
    search_start = 0
    while True:
        match = re.search(r"usage:\s*\{", last_try_content[search_start:])
        if not match:
            break

        json_start_index = search_start + match.end() - 1
        
        open_braces = 1
        i = json_start_index + 1
        while i < len(last_try_content) and open_braces > 0:
            if last_try_content[i] == '{':
                open_braces += 1
            elif last_try_content[i] == '}':
                open_braces -= 1
            i += 1
        
        if open_braces == 0:
            # Found the matching brace, extract the JSON string
            usage_str = last_try_content[json_start_index:i]
            usage_matches.append(usage_str)
            # Continue searching from where we left off
            search_start = i
        else:
            # No matching brace found, stop searching in this content
            break
    file_prompt = 0
    file_complete = 0

    for usage_str in usage_matches:
        try:
            # print(usage_str)
            usage_data = ast.literal_eval(usage_str.replace('null', 'None'))
            
            prompt_tokens = usage_data.get("prompt_tokens") or 0
            completion_tokens = usage_data.get("completion_tokens") or 0
            
            if "reasoning_tokens" in usage_data and usage_data["reasoning_tokens"] is not None:
                completion_tokens += usage_data["reasoning_tokens"]

            file_prompt += prompt_tokens
            file_complete += completion_tokens
            
        except (ValueError, SyntaxError) as e:
            print(usage_str)
    if verbose:
        print(f"Prompt Tokens: {file_prompt}")
        print(f"Completion Tokens: {file_complete}")
    return file_prompt, file_complete


def get_last_function_calls(file_path):
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            if isinstance(data, list) and data:
                last_block = data[-1]
                if isinstance(last_block, dict):
                    return {
                        'function_calls': last_block.get('function_calls'),
                        'completion_tokens': last_block.get('completion_tokens'),
                        'prompt_tokens': last_block.get('prompt_tokens')
                    }
    except (json.JSONDecodeError, FileNotFoundError):
        return None
    return None
def get_all_turns(file_path):
    try:
        turns = 0
        with open(file_path, 'r') as f:
            data = json.load(f)
            if isinstance(data, list) and data:
                for d in data:
                    if "role" in d and d["role"] == "assistant":
                        turns += 1
        return turns
    except (json.JSONDecodeError, FileNotFoundError):
        return 0

def calculate_gemini_cost(log_dir, verbose=True):
    output_dir = os.path.join(os.path.dirname(log_dir), 'output')
    if not os.path.isdir(output_dir):
        assert False
        return 0.0

    total_cost = 0.0

    for filename in os.listdir(output_dir):
        if filename.endswith(".log"):
            filepath = os.path.join(output_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                continue

            parts = content.split("current_try:")
            if len(parts) < 2:
                continue
            
            last_try_content = parts[-1]

            usage_matches = []
            search_start = 0
            while True:
                match = re.search(r"usage:\s*\{", last_try_content[search_start:])
                if not match:
                    break

                json_start_index = search_start + match.end() - 1
                
                open_braces = 1
                i = json_start_index + 1
                while i < len(last_try_content) and open_braces > 0:
                    if last_try_content[i] == '{':
                        open_braces += 1
                    elif last_try_content[i] == '}':
                        open_braces -= 1
                    i += 1
                
                if open_braces == 0:
                    usage_str = last_try_content[json_start_index:i]
                    usage_matches.append(usage_str)
                    search_start = i
                else:
                    break

            for usage_str in usage_matches:
                try:
                    usage_data = ast.literal_eval(usage_str.replace('null', 'None'))
                    
                    prompt_tokens = usage_data.get("prompt_tokens") or 0
                    completion_tokens = usage_data.get("completion_tokens") or 0
                    
                    if "reasoning_tokens" in usage_data and usage_data["reasoning_tokens"] is not None:
                        completion_tokens += usage_data["reasoning_tokens"]

                    prompt_cost = 0.0
                    if prompt_tokens <= 200_000:
                        prompt_cost = (prompt_tokens / 1_000_000) * 1.25
                    else:
                        prompt_cost = (prompt_tokens / 1_000_000) * 2.5

                    completion_cost = 0.0
                    if completion_tokens <= 200_000:
                        completion_cost = (completion_tokens / 1_000_000) * 10
                    else:
                        completion_cost = (completion_tokens / 1_000_000) * 15
                    
                    total_cost += prompt_cost + completion_cost

                except (ValueError, SyntaxError) as e:
                    print(f" {filename} {usage_str[:100]} {e}")
    
    return total_cost

def process_directory(log_dir, verbose=True):
    if not os.path.isdir(log_dir):
        print(f"Error: Directory not found at {log_dir}")
        return [], [], []

    latest_files = {}
    for filename in os.listdir(log_dir):
        if os.path.isfile(os.path.join(log_dir, filename)) and filename.endswith('.txt'):
            try:
                parts = filename[:-4].rsplit('_', 1)
                if len(parts) == 2:
                    instance_id, try_num_str = parts
                    try_num = int(try_num_str)
                    if instance_id not in latest_files or try_num > latest_files[instance_id][0]:
                        latest_files[instance_id] = (try_num, filename)
            except ValueError:
                assert False
                # Ignore files that don't match the pattern
                continue
    sorted_instance_ids = sorted(latest_files.keys())
    for k in selected_instances:
        if k not in sorted_instance_ids:
            print(k)
            # assert False 

    instance_function_calls_in_dir = []
    completion_tokens_in_dir = []
    prompt_tokens_in_dir = []
    # assert len(sorted_instance_ids) == 100
    for instance_id in sorted_instance_ids:
        _try_num, filename = latest_files[instance_id]
        file_path = os.path.join(log_dir, filename)
        result = get_last_function_calls(file_path)
        if result and result.get('function_calls') is not None:
            # gpt has parallel tool call so we use the number of assistant turns
            if "gpt" in log_dir:
                instance_function_calls_in_dir.append((instance_id, get_all_turns(file_path)))
            else:
                instance_function_calls_in_dir.append((instance_id, result['function_calls']))
            if "gemini" in log_dir:
                prompt_tokens, completion_tokens = calculate_gemini_token(log_dir, instance_id, verbose)
                completion_tokens_in_dir.append(completion_tokens)
                prompt_tokens_in_dir.append(prompt_tokens)
            else:
                completion_tokens_in_dir.append(result.get('completion_tokens', 0))
                prompt_tokens_in_dir.append(result.get('prompt_tokens', 0))
            if verbose:
                print(f'  Function Calls: {result.get("function_calls")}')
                print(f'  Completion Tokens: {result.get("completion_tokens")}')
                print(f'  Prompt Tokens: {result.get("prompt_tokens")}')
        else:
            if verbose:
                print('  No relevant data found in the last block.')
        if verbose:
            print('-' * 20)
    return instance_function_calls_in_dir, completion_tokens_in_dir, prompt_tokens_in_dir

def calculate_cost(model_name, total_prompt_tokens, total_completion_tokens):
    cost = 0
    if model_name == 'claude':
        cost = (total_prompt_tokens / 1_000_000) * 3 + (total_completion_tokens / 1_000_000) * 15
    elif model_name == 'gpt':
        cost = (total_prompt_tokens / 1_000_000) * 2 + (total_completion_tokens / 1_000_000) * 8
    elif model_name == 'gemini':
        assert False

    return cost
