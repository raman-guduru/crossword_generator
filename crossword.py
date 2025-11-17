from collections import namedtuple
from itertools import *
from timeit import default_timer as timer
import argparse
import sys
import io

from z3 import *  # Provided by `pip install z3-solver==4.11.2.0`

# Fix Windows console encoding for Unicode box-drawing characters
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


# Decorator for tracking progress and runtime
def print_time(msg):
    def decorator(f):
        def wrapper(*args, **kwargs):
            print(f'{msg} ... ', end='', flush=True)
            start = timer()
            res = f(*args, **kwargs)
            print('{:.2f}s'.format(timer() - start))
            return res

        return wrapper

    return decorator


Placement = namedtuple('Placement', ['x', 'y', 'horizontal'])


def generateCrossword(words, size, minQuality):
    # Input validation
    words = list(set(words))
    assert minQuality >= 0
    assert size > 0, f'Grid size ({size}) too small'
    assert size >= max(len(w) for w in words), \
        f'"{max(words, key=len)}" has more than size={size} characters'

    # Encode valid word placements (over some set of placement variables)
    constraints, placement_vars = encodeProblem(words, size, minQuality)

    # Dump CNF (for experimenting with SAT solvers)
    exportCNF('crossword.cnf', constraints)

    # Solve SMT instance & pretty-print result (if one exists)
    model = solve(constraints)
    if model:
        placement = interpret(model, placement_vars)
        printPlacement(placement, size)
    else:
        print("Constraints unsatisfiable")


@print_time("Encoding")
def encodeProblem(words, size, minQuality):
    # Variables encoding the placement of each word, i.e. setting
    # `placement_vars['doom'][0][3][1]` to `True` denotes
    # 'doom' being placed vertically at (x=3, y=0)
    placement_vars = {word: [[[Bool(f'{word}_{x},{y}_{orientation}')
                               for orientation in ['horizontal', 'vertical']]
                              for x in range(size)]
                             for y in range(size)]
                      for word in words}

    # Variables encoding the subset of words actually put on the grid
    word_selection = {word: Bool(f'{word}_selected') for word in words}

    # Constants representing the words' characters (and "no character")
    chars = list(set("".join(words)))
    char_sort, char_constants = EnumSort('Chars', chars + ['empty'])
    char_empty = char_constants[-1]
    chars_enc = {c: sym for c, sym in zip(chars, char_constants)}

    # Variables encoding the character in each grid cell
    grid = [[Const(f'grid_{x}_{y}', char_sort) for x in range(size)]
            for y in range(size)]

    # `possible_placements[y][x][0]` will contain the placement variables
    # of all words what can be placed horizontally at coord (x,y)
    possible_placements = [[[[]
                             for orientation in ['horizontal', 'vertical']]
                            for x in range(size)]
                           for y in range(size)]

    # Word placement determines characters on grid
    res = []
    for word in words:
        word_placement_vars = []
        for x, y in product(range(size), repeat=2):
            # Fits horizontally
            if x + len(word) <= size:
                # Keep track that this is a possible placement
                word_placement_vars.append(placement_vars[word][y][x][0])
                possible_placements[y][x][0].append(placement_vars[word][y][x][0])

                # Effect (of this placement) on grid
                word_symbols = grid[y][x:x + len(word)]
                match_expr = And([chars_enc[c] == sym for c, sym in zip(word, word_symbols)])
                res.append(Implies(placement_vars[word][y][x][0], match_expr))

                # Word must be bounded by spaces (or grid borders)
                bounding_chars = []
                if x - 1 >= 0:
                    bounding_chars.append(grid[y][x - 1])
                if x + len(word) < size:
                    bounding_chars.append(grid[y][x + len(word)])
                bounded_by_spaces = And([sym == char_empty for sym in bounding_chars])
                res.append(Implies(placement_vars[word][y][x][0], bounded_by_spaces))

            # Fits vertically (analogous to the above case)
            if y + len(word) <= size:
                # Keep track that this is a possible placement
                word_placement_vars.append(placement_vars[word][y][x][1])
                possible_placements[y][x][1].append(placement_vars[word][y][x][1])

                # Effect (of this placement) on grid
                word_symbols = [grid[y + i][x] for i in range(len(word))]
                match_expr = And([chars_enc[c] == sym for c, sym in zip(word, word_symbols)])
                res.append(Implies(placement_vars[word][y][x][1], match_expr))

                # Word must be bounded by spaces (or grid borders)
                bounding_chars = []
                if y - 1 >= 0:
                    bounding_chars.append(grid[y - 1][x])
                if y + len(word) < size:
                    bounding_chars.append(grid[y + len(word)][x])
                bounded_by_spaces = And([sym == char_empty for sym in bounding_chars])
                res.append(Implies(placement_vars[word][y][x][1], bounded_by_spaces))

        # If the word is selected, exactly one placement must be used
        res.append(AtMost(*word_placement_vars, 1))
        res.append(Implies(word_selection[word], Or(word_placement_vars)))

    # Every non-empty sequence (of length > 1) must match a word
    for x, y in product(range(size), repeat=2):
        # Start of horizontal sequence
        if x + 1 < size:
            seq_start = And(grid[y][x - 1] == char_empty if x - 1 >= 0 else True,
                            grid[y][x] != char_empty,
                            grid[y][x + 1] != char_empty)
            res.append(seq_start == Or(possible_placements[y][x][0]))

        # Start of vertical sequence (analogous to the above case)
        if y + 1 < size:
            seq_start = And(grid[y - 1][x] == char_empty if y - 1 >= 0 else True,
                            grid[y][x] != char_empty,
                            grid[y + 1][x] != char_empty)
            res.append(seq_start == Or(possible_placements[y][x][1]))

    # Require grid symbols to form a single connected component (CC)
    ccStartRow = [Bool(f'ccStart_{y}') for y in range(size)]
    ccStart = [[Bool(f'ccStart_{x},{y}') for x in range(size)]
               for y in range(size)]
    inCc = [[[Bool(f'reach{i}_{x},{y}') for i in range(maxDistance(size) + 1)]
             for x in range(size)] for y in range(size)]
    for y in range(size):
        # CC starts in row y
        notInPrevRows = And([Not(ccStartRow[i]) for i in range(y)])
        inCurRow = Or([grid[y][x] != char_empty for x in range(size)])
        res.append(And(inCurRow, notInPrevRows) == ccStartRow[y])

        for x in range(size):
            # CC starts at x,y
            notInPrevPos = And([Not(ccStart[y][i]) for i in range(x)])
            inCurPos = grid[y][x] != char_empty
            res.append(And(ccStartRow[y], inCurPos, notInPrevPos) == ccStart[y][x])

            # Only CC start position reaches itself in 0 steps
            res.append(ccStart[y][x] == inCc[y][x][0])

            # Symbol at x,y reaches CC start in `i` steps if
            # - it already reaches it in `i-1` steps, or
            # - neighbour symbol reaches it in `i-1` steps
            for i in range(1, maxDistance(size) + 1):
                reasons = [inCc[y][x][i - 1]]
                if x - 1 >= 0: reasons.append(inCc[y][x - 1][i - 1])
                if x + 1 < size: reasons.append(inCc[y][x + 1][i - 1])
                if y - 1 >= 0: reasons.append(inCc[y - 1][x][i - 1])
                if y + 1 < size: reasons.append(inCc[y + 1][x][i - 1])
                res.append(Implies(inCc[y][x][i],
                                   And(grid[y][x] != char_empty, Or(reasons))))

            # All non-empty grid entries must reach the CC start
            res.append(Implies(grid[y][x] != char_empty, inCc[y][x][maxDistance(size)]))

    # Require the solution to satisfy some quality criterion
    # Here: Quality corresponds to the sum of the selected words' lengths
    res.append(PbGe([(var, len(w)) for w, var in word_selection.items()], minQuality))

    return res, placement_vars


@print_time("CNF export")
def exportCNF(filepath, assertions):
    goal = Goal()
    goal.add(assertions)

    p = ParamsRef()
    p.set('pb.solver', 'binary_merge')  # use any setting but 'solver'
    to_cnf = WithParams(Then('simplify', 'dt2bv', 'card2bv', 'bit-blast', 'tseitin-cnf'), p)
    subgoals = to_cnf(goal)
    assert len(subgoals) == 1, "Tactic should have resulted in a single goal"
    assert not subgoals[0].inconsistent(), "Found to be UNSAT during pre-processing"
    with open(filepath, 'w') as f:
        f.write(subgoals[0].dimacs() + '\n')


@print_time("Solving")
def solve(constraints):
    # Solve via quantifier-free finite domain solver
    s = SolverFor('QF_FD')
    s.add(constraints)
    return s.model() if s.check() == sat else None


def interpret(model, placement_vars):
    placement = dict()
    for word, word_placement_vars in sorted(placement_vars.items()):
        for y in range(len(word_placement_vars)):
            for x in range(len(word_placement_vars[y])):
                if is_true(model.eval(word_placement_vars[y][x][0])):
                    placement[word] = Placement(x, y, True)
                elif is_true(model.eval(word_placement_vars[y][x][1])):
                    placement[word] = Placement(x, y, False)

    return placement


def printPlacement(placement, size):
    # Pretty print placement details
    placed_symbols = sum([len(w) for w in placement.keys()])
    print(f'Placed {len(placement)} words ({placed_symbols} symbols):')
    for i, word in enumerate(placement):
        print('{:2d}) {} {}'.format(i + 1, word, placement[word]))

    # Fill explicit grid with characters from interpretation (rest are spaces)
    grid = [[' ' for x in range(size)] for y in range(size)]
    for word, placement in placement.items():
        for i, c in enumerate(word):
            x = placement.x + i if placement.horizontal else placement.x
            y = placement.y if placement.horizontal else placement.y + i
            grid[y][x] = c

    # Pretty print grid with Unicode box-drawing characters
    # Wrapped in try-except to fall back to ASCII if Unicode fails
    try:
        print('┌' + '┬'.join('─' * size) + '┐')
        for y, row in enumerate(grid):
            if y != 0 and y != len(grid):
                print('├' + '┼'.join('─' * size) + '┤')
            print('│' + '│'.join(row) + '│')
        print('└' + '┴'.join('─' * size) + '┘')
    except UnicodeEncodeError:
        # Fallback to ASCII box drawing
        print('+' + '+'.join('-' * size) + '+')
        for y, row in enumerate(grid):
            if y != 0 and y != len(grid):
                print('+' + '+'.join('-' * size) + '+')
            print('|' + '|'.join(row) + '|')
        print('+' + '+'.join('-' * size) + '+')


# Max distance between placed characters (if they form a component)
def maxDistance(size):
    return (size + 1) ** 2 // 2 - 1  # Tighter bound than size**2


def main():
    parser = argparse.ArgumentParser(description="Generate a crossword puzzle from a word list.")
    parser.add_argument("word_file", help="Path to a text file containing words, one per line.")
    parser.add_argument("size", type=int, help="The size of the grid (size x size).")
    parser.add_argument("min_quality", type=int, help="The minimum quality score (sum of lengths of placed words).")

    args = parser.parse_args()

    try:
        with open(args.word_file, 'r', encoding='utf-8') as f:
            # Read words, strip whitespace, convert to uppercase, and ignore empty lines
            words = [line.strip().upper() for line in f if line.strip()]
        
        if not words:
            print(f"Error: No words found in '{args.word_file}'.")
            sys.exit(1)
    except FileNotFoundError:
        print(f"Error: Word file not found at '{args.word_file}'")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file: {e}")
        sys.exit(1)

    generateCrossword(words, args.size, args.min_quality)


if __name__ == '__main__':
    main()