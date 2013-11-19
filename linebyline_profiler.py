"""CPU profiler that works by collecting line-by-line stats.

This works by storing a list of functions to profile, then telling
the third party line_profiler module to profile those functions.
"""

import collections
import inspect
import linecache
import os
import re
import sys

_is_dev_server = os.environ["SERVER_SOFTWARE"].startswith("Devel")

# We can't use LineProfiler in production because it requires a C-extension,
# but we can monkey-patch it in here for use on the dev server:
if _is_dev_server:
    if os.environ["SERVER_SOFTWARE"] == "Development/2.0":
        # module name looks something like 'gae_mini_profiler._line_profiler'
        sys.meta_path[3]._enabled_regexes.append(
                re.compile(r'(?:.*\.)?_line_profiler$'))
    else:
        from google.appengine.tools import dev_appserver
        if isinstance(sys.meta_path[0], dev_appserver.HardenedModulesHook):
            sys.meta_path[0]._white_list_c_modules += ['_line_profiler']

    import line_profiler
    assert line_profiler  # Silence pyflakes
else:
    line_profiler = None

_FUNCTION_MARKER = "__gae_linebyline_profile"

_functions_to_profile = []


def line_profile(f):
    """The passed function will be included in the line profile displayed by
    the line profiler panel.
    """
    # TODO(jlfwong): See if this is needed.
    f.__dict__[_FUNCTION_MARKER] = True
    if f not in _functions_to_profile:
        _functions_to_profile.append(f)

    return f


def _process_line_stats(line_stats):
    """Convert line_profiler.LineStats instance into a dict.

    The returned dict has the following format:

        [{
            "filename": the filename of the function being profiled
            "start_lineno": the first line number of the function
            "func_name": the name of the function
            "total_time_ms": total time spent inside the function in ms
            "total_time_ms_s": formatted string version of above
            "timings": [{
                'lineno': line number being profiled
                'line': string source line being profiled
                'perc_time': percent of total time spent on this line
                'perc_time_s': formatted string version of above
                'time_ms': total time spent on this line
                'time_ms_s': formatted string version of above
                'numhits': the number of times this line was run
            }, ...]
        }, ...]
    """

    profile_results = []

    if not line_stats:
        return profile_results

    # We want timings in ms (instead of CPython's microseconds)
    multiplier = line_stats.unit / 1e-3

    for key, timings in sorted(line_stats.timings.items()):
        if not timings:
            continue

        filename, start_lineno, func_name = key

        all_lines = linecache.getlines(filename)
        sublines = inspect.getblock(all_lines[start_lineno - 1:])
        end_lineno = start_lineno + len(sublines)

        line_to_timing = collections.defaultdict(lambda: (-1, 0))

        for (lineno, nhits, time) in timings:
            line_to_timing[lineno] = (nhits, time)

        padded_timings = []

        for lineno in range(start_lineno, end_lineno):
            nhits, time = line_to_timing[lineno]
            padded_timings.append((lineno, nhits, time))

        timings = []

        result = {
            'filename': filename,
            'start_lineno': start_lineno,
            'func_name': func_name,
            'total_time_ms': (sum([time for _, _, time in padded_timings]) *
                    multiplier),
            'timings': []
        }

        result['total_time_ms_s'] = '%.0f' % result['total_time_ms']

        for (lineno, nhits, time) in padded_timings:
            time_ms = time * multiplier
            perc_time = (100.0 * time_ms) / result['total_time_ms']

            result['timings'].append({
                'lineno': lineno,
                'line': all_lines[lineno - 1],
                'perc_time': perc_time,
                'perc_time_s': '%.1f' % perc_time,
                'time_ms': time_ms,
                'time_ms_s': "%.2f" % time_ms,
                'numhits': nhits
            })

        profile_results.append(result)

    return profile_results


class Profile(object):
    """Profiler wrapping line_profiler."""
    def __init__(self):
        self.num_functions_marked = len(_functions_to_profile)

        if line_profiler is None:
            self.line_prof = None
        else:
            self.line_prof = line_profiler.LineProfiler()

            for f in _functions_to_profile:
                self.line_prof.add_function(f)

    def results(self):
        res = {
            "is_dev_server": _is_dev_server,
            "num_functions_marked": self.num_functions_marked,
            "calls": []
        }

        if self.line_prof and self.num_functions_marked:
            res["calls"] = _process_line_stats(self.line_prof.get_stats())

        return res

    def run(self, fxn):
        if self.line_prof is None:
            return fxn()
        else:
            return self.line_prof.runcall(fxn)
