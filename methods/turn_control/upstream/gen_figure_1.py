import json
import os
import matplotlib.pyplot as plt

def analyze_trajectories_refact(directory):
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
                    count = 0
                    for item in data:
                        if isinstance(item, dict) and item.get('role') == 'assistant':
                            count += 1
                    assistant_counts.append(count)
            except json.JSONDecodeError:
                print(f"Error decoding JSON from file: {filename}")
            except Exception as e:
                print(f"An error occurred with file {filename}: {e}")

    return assistant_counts

def analyze_trajectories_claude(directory):
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
        if filename.endswith('.txt'):
            file_path = os.path.join(directory, filename)
            try:
                with open(file_path, 'r') as f:
                    data = f.read()
                    count = data.count('</antml:function_calls>')
                    assistant_counts.append(count)
            except json.JSONDecodeError:
                print(f"Error decoding JSON from file: {filename}")
            except Exception as e:
                print(f"An error occurred with file {filename}: {e}")

    return assistant_counts

def analyze_trajectories_warp(directory):
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
        if filename.endswith('.txt'):
            file_path = os.path.join(directory, filename)
            try:
                with open(file_path, 'r') as f:
                    data = f.read()
                    import re
                    match = re.search(r'Total number of exchanges: (\d+)', data)
                    if match:
                        count = int(match.group(1))
                    else:
                        count = 0
                    assert count != 0
                    assistant_counts.append(count)
            except json.JSONDecodeError:
                print(f"Error decoding JSON from file: {filename}")
            except Exception as e:
                print(f"An error occurred with file {filename}: {e}")

    return assistant_counts


if __name__ == "__main__":

    refact_directory = './top_5_agents/20250603_Refact_Agent_claude-4-sonnet/trajs'
    trae_directory = './top_5_agents/20250612_trae/trajs'
    claude_opus_directory = './top_5_agents/20250522_tools_claude-4-opus/trajs'
    claude_sonnet_directory = './top_5_agents/20250522_tools_claude-4-sonnet/trajs'
    warp_directory = './top_5_agents/20250623_warp/trajs'

    warp = analyze_trajectories_warp(warp_directory)

    refact = analyze_trajectories_refact(refact_directory)

    trae = analyze_trajectories_refact(trae_directory)

    claude_opus = analyze_trajectories_claude(claude_opus_directory)

    claude_sonnet = analyze_trajectories_claude(claude_sonnet_directory)

    data_to_plot = [trae, refact, claude_opus, claude_sonnet, warp]

    # Create a figure and axes
    fig, ax = plt.subplots()

    # Create the boxplot
    bp = ax.boxplot(data_to_plot, patch_artist=True, vert=True)

    ax.set_xticklabels(['TRAE', 'Refact.ai Agent', 'Tools + \nClaude 4 \nOpus', 'Tools + \nClaude 4 \nSonnet', 'Warp'])
    ax.set_ylabel('Number of Turns')
    ax.set_title('Number of Turns Needed by Different Agents')

    # Adding colors to the boxplots
    colors = ['#1f77b4', '#2ca02c', '#d62728', '#9467bd', '#8c564b']
    for patch, color in zip(bp['boxes'], colors):
        patch.set_facecolor(color)

    # Print median values
    labels = ax.get_xticklabels()
    print("Median values:")
    for i, line in enumerate(bp['medians']):
        median_value = line.get_ydata()[0]
        label_text = labels[i].get_text().replace('\n', ' ')
        print(f"{label_text}: {median_value:.1f}")

    # Add a grid
    ax.yaxis.grid(True)

    # Save the plot
    plt.savefig('function_calls_boxplot.pdf')

    print("Boxplot saved as function_calls_boxplot.pdf")
