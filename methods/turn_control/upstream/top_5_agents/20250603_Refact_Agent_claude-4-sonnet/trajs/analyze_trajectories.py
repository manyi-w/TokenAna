import json
import os

def analyze_trajectories(directory):
    """
    Analyzes all JSON trajectory files in a directory to count the number of 'assistant' roles.

    Args:
        directory (str): The path to the directory containing the trajectory files.

    Returns:
        list: A list of integers, where each integer is the count of 'assistant' roles
              in a corresponding trajectory file.
    """
    assistant_counts = []
    if not os.path.isdir(directory):
        print(f"Error: Directory not found at {directory}")
        return assistant_counts

    for filename in sorted(os.listdir(directory)):
        if filename.endswith('.json'):
            file_path = os.path.join(directory, filename)
            try:
                with open(file_path, 'r') as f:
                    data = json.load(f)
                    # The trajectory is in a list under the 'trajectory' key
                    trajectory = data.get('trajectory', [])
                    count = 0
                    for item in trajectory:
                        if isinstance(item, dict) and item.get('role') == 'assistant':
                            count += 1
                    assistant_counts.append(count)
            except json.JSONDecodeError:
                print(f"Error decoding JSON from file: {filename}")
            except Exception as e:
                print(f"An error occurred with file {filename}: {e}")

    return assistant_counts

if __name__ == "__main__":
    # The user specified a different directory in the prompt.
    # Using the directory from the user's request.
    target_directory = '/mnt/swebench-evaluation/verified/20250603_Refact_Agent_claude-4-sonnet/trajs'
    counts = analyze_trajectories(target_directory)
    print(f"Found {len(counts)} trajectory files.")
    print("Counts of 'assistant' roles:")
    print(counts)
