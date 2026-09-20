"""Stream transport logs to persistent files, including hard interruptions."""
import subprocess


def run_logged(command, directory, **kwargs):
    kwargs.pop('capture_output', None)
    out_path, err_path = directory / 'live-stdout.txt', directory / 'live-stderr.txt'
    with out_path.open('w') as out, err_path.open('w') as err:
        try:
            result = subprocess.run(command, stdout=out, stderr=err, **kwargs)
        except subprocess.TimeoutExpired as error:
            error.stdout = out_path.read_text(errors='replace')
            error.stderr = err_path.read_text(errors='replace')
            raise
    result.stdout, result.stderr = out_path.read_text(errors='replace'), err_path.read_text(errors='replace')
    return result
