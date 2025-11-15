from collections import namedtuple
from itertools import *
from timeit import default_timer as timer
import argparse

from z3 import *

# --- Hex Grid Helper Functions ---

def get_hex_neighbors(q, r):
    """Returns the 6 neighbors of a hex cell in axial coordinates."""
    return [
        (q + 1, r), (q - 1, r),
        (q, r + 1), (q, r - 1),
        (q + 1, r - 1), (q - 1, r + 1)
    ]

def get_hex_cells_in_radius(radius):
    """Returns all (q, r) coordinates for a hexagonal grid of a given radius."""
    cells = []
    for q in range(-radius, radius + 1):
        for r in range(-radius, radius + 1):
            # The third cube coordinate s is implicitly -q-r
            if abs(q) + abs(r) + abs(-q - r) <= 2 * radius:
                cells.append((q, r))
    return cells

# --- Z3 Crossword Logic ---

def print_time(msg):
    def decorator(f):
        def wrapper(*args, **kwargs):
            print(f'{msg} ... ', end='')
            start = timer()
            res = f(*args, **kwargs)
            print('{:.2f}s'.format(timer() - start))
            return res
        return wrapper
    return decorator

Placement = namedtuple('Placement', ['q', 'r', 'orientation'])

@print_time("Encoding")
def encodeProblem(words, radius, minQuality, longest_word_to_pin=None):
    # Define the set of valid coordinates for our hexagonal grid
    grid_coords = get_hex_cells_in_radius(radius)
    grid_coords_set = set(grid_coords)

    # Variables for word placements: {word: {(q, r): [orient0, orient1, orient2]}}
    placement_vars = {
        word: {
            (q, r): [Bool(f'{word}_{q},{r}_{o}') for o in range(3)]
            for (q, r) in grid_coords
        }
        for word in words
    }

    word_selection = {word: Bool(f'{word}_selected') for word in words}

    chars = list(set("".join(words)))
    char_sort, char_constants = EnumSort('Chars', chars + ['empty'])
    char_empty = char_constants[-1]
    chars_enc = {c: sym for c, sym in zip(chars, char_constants)}

    # Grid variables: a dictionary mapping (q, r) coords to Z3 character constants
    grid = {(q, r): Const(f'grid_{q}_{r}', char_sort) for (q, r) in grid_coords}

    # Store which placements start at each cell for the "no junk words" constraint
    possible_placements = {(q, r): [[] for _ in range(3)] for (q, r) in grid_coords}
    res = []
    for word in words:
        word_placement_vars = []
        for q, r in grid_coords:
            # Try placing the word in 3 orientations
            for orientation in range(3):
                word_path = []
                valid_placement = True
                for i in range(len(word)):
                    if orientation == 0: # Constant r
                        cell = (q + i, r)
                    elif orientation == 1: # Constant q
                        cell = (q, r + i)
                    else: # Constant s = -q-r
                        cell = (q + i, r - i)

                    if cell not in grid_coords_set:
                        valid_placement = False
                        break
                    word_path.append(cell)

                if valid_placement:
                    p_var = placement_vars[word][(q, r)][orientation]
                    word_placement_vars.append(p_var)
                    possible_placements[(q, r)][orientation].append(p_var)

                    # Constraint: Placement implies grid characters
                    word_symbols = [grid[cell] for cell in word_path]
                    match_expr = And([chars_enc[c] == sym for c, sym in zip(word, word_symbols)])
                    res.append(Implies(p_var, match_expr))

                    # Constraint: Word must be bounded by empty cells
                    bounding_cells = []
                    # Cell before the start
                    if orientation == 0: prev_cell = (q - 1, r)
                    elif orientation == 1: prev_cell = (q, r - 1)
                    else: prev_cell = (q - 1, r + 1)
                    if prev_cell in grid: bounding_cells.append(grid[prev_cell])

                    # Cell after the end
                    if orientation == 0: next_cell = (q + len(word), r)
                    elif orientation == 1: next_cell = (q, r + len(word))
                    else: next_cell = (q + len(word), r - len(word))
                    if next_cell in grid: bounding_cells.append(grid[next_cell])

                    bounded = And([sym == char_empty for sym in bounding_cells])
                    res.append(Implies(p_var, bounded))

        # Constraint: A selected word is placed at most once
        res.append(AtMost(*word_placement_vars, 1))
        res.append(Implies(word_selection[word], Or(word_placement_vars)))

    # Constraint: Any sequence of letters must be a valid word from the list
    for q, r in grid_coords:
        for orientation in range(3):
            # Define start of a sequence in this orientation
            if orientation == 0:
                prev_cell = (q - 1, r)
                curr_cell = (q, r)
                next_cell = (q + 1, r)
            elif orientation == 1:
                prev_cell = (q, r - 1)
                curr_cell = (q, r)
                next_cell = (q, r + 1)
            else: # orientation 2
                prev_cell = (q - 1, r + 1)
                curr_cell = (q, r)
                next_cell = (q + 1, r - 1)

            # Check if sequence start is valid
            if curr_cell in grid and next_cell in grid:
                is_prev_empty = grid[prev_cell] == char_empty if prev_cell in grid else True
                seq_start = And(is_prev_empty, grid[curr_cell] != char_empty, grid[next_cell] != char_empty)
                res.append(seq_start == Or(possible_placements[(q, r)][orientation]))
    # Constraint: All letters form a single connected component (CC)
    max_dist = len(grid_coords) // 2
    cc_start = {(q, r): Bool(f'cc_start_{q}_{r}') for (q, r) in grid_coords}
    in_cc = [[Bool(f'in_cc_{i}_{q}_{r}') for q, r in grid_coords] for i in range(max_dist + 1)]

    # Define which cell is the start cell: the first non-empty one in reading order
    prev_cells_empty = []
    for q, r in grid_coords: # Assumes grid_coords is sorted, which it is.
        res.append(cc_start[(q, r)] == And(grid[(q, r)] != char_empty, And(prev_cells_empty)))
        prev_cells_empty.append(grid[(q, r)] == char_empty)

    # Propagate reachability step-by-step
    for i, (q, r) in product(range(max_dist + 1), grid_coords):
        if i == 0:
            res.append(in_cc[i][grid_coords.index((q, r))] == cc_start[(q, r)])
        else:
            reasons = [in_cc[i-1][grid_coords.index((q, r))]] # Reachable in previous step
            for n_q, n_r in get_hex_neighbors(q, r):
                if (n_q, n_r) in grid_coords_set:
                    reasons.append(in_cc[i-1][grid_coords.index((n_q, n_r))])
            res.append(in_cc[i][grid_coords.index((q, r))] == And(grid[(q, r)] != char_empty, Or(reasons)))

    # All non-empty cells must be in the component
    for q, r in grid_coords:
        res.append(Implies(grid[(q, r)] != char_empty, in_cc[max_dist][grid_coords.index((q, r))]))

    # Constraint: Minimum quality
    res.append(PbGe([(var, len(w)) for w, var in word_selection.items()], minQuality))

    return res, placement_vars, grid

@print_time("CNF export")
def exportCNF(filepath, assertions):
    goal = Goal()
    goal.add(assertions)

    p = ParamsRef()
    p.set('pb.solver', 'binary_merge')
    to_cnf = WithParams(Then('simplify', 'dt2bv', 'card2bv', 'bit-blast', 'tseitin-cnf'), p)
    subgoals = to_cnf(goal)
    assert len(subgoals) == 1, "Tactic should have resulted in a single goal"
    if subgoals[0].inconsistent():
        print("Warning: UNSAT found during CNF conversion.")
        # Return False to indicate failure
        return False
    with open(filepath, 'w') as f:
        f.write(subgoals[0].dimacs() + '\n')

@print_time("Solving")
def solve(constraints, timeout_ms):
    s = SolverFor('QF_FD')
    s.set('timeout', timeout_ms)
    s.add(constraints)
    result = s.check()
    return result, s.model() if result == sat else None

def interpret(model, placement_vars, grid_coords):
    placement = {}
    for word, p_vars_by_cell in sorted(placement_vars.items()):
        for (q, r), p_vars_by_orient in p_vars_by_cell.items():
            for orientation, p_var in enumerate(p_vars_by_orient):
                if is_true(model.eval(p_var)):
                    placement[word] = Placement(q, r, orientation)
                    break
            if word in placement:
                break
    return placement

def printPlacement(placement, radius):
    print(f'Placed {len(placement)} words ({sum(len(w) for w in placement)} symbols):')
    for i, (word, p) in enumerate(placement.items()):
        print(f'{i+1:2d}) {word} @ ({p.q},{p.r}) orient={p.orientation}')

    # For printing, we map axial hex coords to a double-resolution 2D console grid.
    grid_coords = get_hex_cells_in_radius(radius)
    min_q = min(c[0] for c in grid_coords)
    min_r = min(c[1] for c in grid_coords)

    # Determine the required size of the character grid
    max_col, max_row = 0, 0
    for q, r in grid_coords: # Using double-resolution grid
        col = (q - min_q) * 3
        row = (r - min_r) * 2 + (q - min_q)
        max_col = max(max_col, col)
        max_row = max(max_row, row)

    char_grid = [[' ' for _ in range(max_col + 3)] for _ in range(max_row + 3)]

    # Mark valid hex positions with a '.'
    for q, r in grid_coords:
        col = (q - min_q) * 3
        row = (r - min_r) * 2 + (q - min_q)
        char_grid[row][col] = '.'

    for word, p in placement.items():
        for i, char in enumerate(word):
            col = (p.q - min_q) * 3 + i * [3, 0, 3][p.orientation]
            row = (p.r - min_r) * 2 + (p.q - min_q) + i * [1, 2, -1][p.orientation]

            if 0 <= row < len(char_grid) and 0 <= col < len(char_grid[0]):
                char_grid[row][col] = char

    print("\n--- Hexagonal Grid ---")
    for row_list in char_grid:
        # Don't print completely empty lines at the top/bottom
        if any(c.strip() for c in row_list):
            print(" ".join(row_list))
    print("----------------------")

def generateHexCrossword(words, radius, minQuality, timeout_sec, cnf_file, break_symmetry):
    print("--- Generating Hexagonal Crossword Puzzle ---")
    words = list(set(w.upper() for w in words if w))
    max_len = max((len(w) for w in words), default=0)
    assert radius > 0, "Grid radius must be positive"
    assert 2 * radius + 1 >= max_len, \
        f'"{max(words, key=len)}" (len {max_len}) is too long for grid with radius {radius}'
    
    longest_word = max(words, key=len) if break_symmetry and words else None

    constraints, p_vars, grid_vars = encodeProblem(words, radius, minQuality, longest_word_to_pin=longest_word)

    if cnf_file:
        success = exportCNF(cnf_file, constraints)
        if success is False:
            print("\nProblem is unsatisfiable (detected during CNF conversion).")
            return

    result, model = solve(constraints, timeout_sec * 1000)
    
    if result == sat:
        placement = interpret(model, p_vars, grid_vars.keys())
        printPlacement(placement, radius)
    elif result == unsat:
        print("\nConstraints are unsatisfiable. No solution exists.")
        print("Try reducing the min_quality value or providing more words.")
    else:
        print(f"\nSolver timed out after {timeout_sec} seconds.")
        print("The problem may be too complex. Try increasing the timeout, reducing quality, or using a smaller grid.")

def main():
    parser = argparse.ArgumentParser(description="Generate a hexagonal crossword puzzle.")
    parser.add_argument("word_file", help="Path to a text file with words.")
    parser.add_argument("radius", type=int, help="The radius of the hexagonal grid.")
    parser.add_argument("min_quality", type=int, help="Minimum quality (sum of word lengths).")
    parser.add_argument("--timeout", type=int, default=600, help="Solver timeout in seconds (default: 60).")
    parser.add_argument("--cnf", type=str, default="hex_crossword.cnf", help="Export the problem to a CNF file at the given path.")
    parser.add_argument("--no-symmetry-break", action="store_false", dest="break_symmetry", help="Disable symmetry breaking constraint.")

    args = parser.parse_args()

    try:
        with open(args.word_file, 'r') as f:
            words = [line.strip() for line in f]
    except FileNotFoundError:
        print(f"Error: Word file not found at '{args.word_file}'")
        exit(1)

    if not any(words):
        print(f"Error: No words found in '{args.word_file}'.")
        exit(1)

    generateHexCrossword(words, args.radius, args.min_quality, args.timeout, args.cnf, args.break_symmetry)

if __name__ == '__main__':
    main()