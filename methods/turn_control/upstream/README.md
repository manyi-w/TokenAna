# Open Source Artifacts

This directory contains the open-source artifacts for our research paper.

## Directory Structure

- `experiments/`: This directory contains all the experimental results. The subdirectories are organized by the model used (e.g., `claude`, `gemini`, `gpt`) and other experimental parameters.

- `top_5_agents/`: This directory contains the source data for generating Figure 1 in the paper. 

- `gen_figure_1.py`: This Python script can be executed to generate Figure 1 from the data in the `top_5_agents` directory. To run it, use the following command:
  ```bash
  python gen_figure_1.py
  ```

- `generate_table_2.py`: This script generates Table 2 of the paper. To run it, use the following command:
  ```bash
  python generate_table_2.py
  ```

- `generate_table_3.py`: This script generates Table 3 of the paper. To run it, use the following command:
  ```bash
  python generate_table_3.py
  ```

- `generate_table_4.py`: This script generates Table 4 of the paper. To run it, use the following command:
  ```bash
  python generate_table_4.py
  ```

- `swebench_verified_subset.json`: This JSON file contains the subset of SWE-bench instances that we selected for our evaluation.