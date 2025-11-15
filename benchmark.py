import subprocess
import time
from collections import defaultdict

import matplotlib.pyplot as plt

# --- IMPORTANT: UPDATE THESE PATHS ---
# Update these paths to point to your compiled solver executables.
# The keys are the names that will appear on the graph legend.
SOLVERS = {
    "CaDiCaL": "../cadical/build/cadical",
    "Lingeling": "../lingeling/lingeling",
    "CryptoMiniSat": "../cryptominisat5-linux-amd64/cryptominisat5",
}
# -------------------------------------

# --- Benchmark Configuration ---
WORD_FILE = "/home/raman/crossword_generator/hard.txt"
GRID_SIZE = 12
# We will test each quality value in this range
QUALITY_RANGE = range(50,97, 2)
CNF_FILE = "crossword.cnf"
GRAPH_FILE = "benchmark_graph.png"
# -----------------------------

def run_z3_and_export(words, size, quality):
    """
    Runs the crossword generator to get the Z3 solve time and export the CNF file.
    """
    # We need to run this in a separate process to accurately capture
    # the output of the @print_time decorators.
    print(f"Generating problem for quality={quality}...")
    cmd = [
        "python",
        "/home/raman/crossword_generator/crossword.py",
        WORD_FILE,
        str(size),
        str(quality)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Error generating CNF for quality {quality}")
        print(result.stderr)
        return None

    # Parse the output to find the "Solving" time for Z3
    for line in result.stdout.split('\n'):
        if "Solving" in line:
            try:
                # Extracts the time value, e.g., from "Solving ... 1.23s"
                time_str = line.split()[-1].replace('s', '')
                return float(time_str)
            except (ValueError, IndexError):
                return None
    return None

def run_external_solver(solver_path):
    """
    Runs an external solver on the generated CNF file and returns the time.
    """
    cmd = [
        "python",
        "/home/raman/crossword_generator/solve_cnf.py",
        solver_path,
        CNF_FILE,
        "--quiet"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except (ValueError, IndexError):
        print(f"Failed to get time for {solver_path}")
        print(result.stderr)
        return None

if __name__ == "__main__":
    # Use defaultdict to easily append results
    results = defaultdict(list)
    qualities = list(QUALITY_RANGE)

    for quality in qualities:
        # 1. Run Z3 and generate the CNF
        z3_time = run_z3_and_export(WORD_FILE, GRID_SIZE, quality)
        if z3_time is None:
            print(f"Skipping quality {quality} due to generation error.")
            continue
        results["Z3"].append(z3_time)
        print(f"  - Z3: {z3_time:.4f}s")

        # 2. Run external solvers on the generated CNF
        for name, path in SOLVERS.items():
            solve_time = run_external_solver(path)
            if solve_time is not None:
                results[name].append(solve_time)
                print(f"  - {name}: {solve_time:.4f}s")
            else:
                # If a solver fails, add a placeholder (e.g., NaN) to keep lists aligned
                results[name].append(float('nan'))

    # 3. Plot the results
    print("\nGenerating plot...")
    plt.style.use('seaborn-v0_8-whitegrid')
    fig, ax = plt.subplots(figsize=(10, 6))

    for name, times in results.items():
        ax.plot(qualities[:len(times)], times, marker='o', linestyle='-', label=name)

    ax.set_xlabel("Minimum Quality (Sum of Word Lengths)")
    ax.set_ylabel("Runtime (seconds)")
    ax.set_title(f"SAT/SMT Solver Performance on Crossword Generation (Grid Size: {GRID_SIZE}x{GRID_SIZE})")
    ax.legend()
    ax.set_yscale('log') # Use a logarithmic scale for better visualization
    plt.tight_layout()
    
    plt.savefig(GRAPH_FILE)
    print(f"Benchmark graph saved to {GRAPH_FILE}")