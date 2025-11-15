import argparse
import subprocess
import sys
import time

def parse_solver_output(output):
    """
    Parses the standard output of a SAT solver in DIMACS format.
    """
    solution = None
    result = "UNKNOWN"
    lines = output.strip().split('\n')

    for line in lines:
        if line.startswith("s SATISFIABLE"):
            result = "SATISFIABLE"
        elif line.startswith("s UNSATISFIABLE"):
            result = "UNSATISFIABLE"
            return result, None
        elif line.startswith("v "):
            if solution is None:
                solution = []
            # Append values, removing the 'v' and trailing '0'
            solution.extend(line.split()[1:-1])

    return result, solution

def solve_with_external_solver(solver_path, cnf_file, quiet=False):
    """
    Runs an external SAT solver on a given CNF file.
    """
    start_time = time.time()
    if not quiet:
        print(f"Running {solver_path} on {cnf_file}...")

    try:
        process = subprocess.run(
            [solver_path, cnf_file],
            capture_output=True,
            text=True,
            check=True
        )
        end_time = time.time()
        duration = end_time - start_time
        if quiet:
            print(f"{duration:.4f}")
        else:
            print(f"Solver finished in {duration:.4f} seconds.")
        return process.stdout

    except FileNotFoundError:
        print(f"Error: Solver executable not found at '{solver_path}'")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        # Solvers often return non-zero exit codes for UNSAT (e.g., 20)
        # We can still parse the output.
        end_time = time.time()
        duration = end_time - start_time
        if quiet:
            print(f"{duration:.4f}")
        else:
            print(f"Solver finished with a non-zero exit code ({e.returncode}) in {duration:.4f} seconds.")
        return e.stdout
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Solve a CNF file using an external SAT solver.")
    parser.add_argument("solver_path", help="Path to the SAT solver executable.")
    parser.add_argument("cnf_file", help="Path to the input .cnf file (in DIMACS format).")
    parser.add_argument("--quiet", action="store_true", help="Only print the final time in seconds.")

    args = parser.parse_args()

    solver_output = solve_with_external_solver(args.solver_path, args.cnf_file, args.quiet)

    if not args.quiet:
        result, solution = parse_solver_output(solver_output)

        print("-" * 20)
        print(f"Result: {result}")
        print("-" * 20)

        if result == "SATISFIABLE" and solution:
            print(f"Found a satisfying assignment for {len(solution)} variables.")
        elif result == "UNSATISFIABLE":
            print("The formula is unsatisfiable.")