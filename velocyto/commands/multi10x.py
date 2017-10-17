#!/usr/bin/python
import os
import sys
import shutil
import glob
import click
import re
import time
import logging
import subprocess
logging.basicConfig(stream=sys.stdout, format='%(asctime)s - %(levelname)s - %(message)s', level=logging.DEBUG)


@click.command(short_help="Runs the velocity analysis on multiple a Chromium samples in parallel")
@click.argument("parentfolder",
                type=click.Path(exists=True,
                                file_okay=False,
                                dir_okay=True,
                                readable=True,
                                writable=True,
                                resolve_path=True))
@click.argument("ivlfile",
                type=click.Path(exists=True,
                                file_okay=True,
                                dir_okay=False,
                                readable=True,
                                resolve_path=True))
@click.option("--number", "-n",
              help="Number of processes to execute",
              default=5,
              type=click.INT)
@click.option("--wait", "-w",
              help="Delay in seconds between the executions of single run comands",
              default=20,
              type=click.INT)
@click.option("--metadatatable", "-s",
              help="Table containing metadata of the various samples (csv fortmated, [row:samples, col:entry])",
              default=None,
              type=click.Path(resolve_path=True,
                              file_okay=True,
                              dir_okay=False,
                              readable=True))
@click.option("--repmask", "-m",
              help=".gtf file containing intrvals sorted by chromosome, strand, position\n(e.g. generated by running `velocyto extract_repeats mm10_rmsk.gtf`)",
              default=None,
              type=click.Path(resolve_path=True,
                              file_okay=True,
                              dir_okay=False,
                              readable=True))
@click.option("--logfolder", "-l",
              help="Folder where all the log files will be generated",
              default=None,
              type=click.Path(resolve_path=True))
@click.option("--debug", "-d",
              help="debug mode. It will generate .sam files of individual reads (not molecules) that are identified as exons, introns, ambiguous and chimeras",
              default=False,
              is_flag=True)
def multi10x(parentfolder: str, ivlfile: str,
             number: int, wait: int,
             metadatatable: str, repmask: str,
             logfolder: str, debug: bool) -> None:
    """Runs the velocity analysis on multiple a Chromium samples in parallel, spawning several subprocesses
    """
    all_10X_dirs = glob.glob("/data/proj/chromium/10X*")

    logging.debug(f"Attempting to start {number} processes")
    n = 0
    all_10X_dirs.sort()
    for dir_i in all_10X_dirs:
        vc_dir = dir_i + "/velocyto"
        sample_id = dir_i.split("/")[-1]
        name_log_file = os.path.join(logfolder, f'nohup_{sample_id}.out')
        if not os.path.exists(vc_dir):
            try:
                os.mkdir(vc_dir)
            except FileExistsError:
                logging.debug("Wow this the directory was created after we checked! another process is running the sample. Move on.")
                continue
        list_loom = [i for i in os.listdir(vc_dir) if re.match("^10X[_]*?[0-9]{2,3}_[0]*?[1-8]{1,}.loom$", i)]
        if list_loom == [] and not os.path.exists(name_log_file):
            folder = f"/data/proj/chromium/{sample_id}"
            arguments = ["nohup", "velocyto", "run10x", f"{folder}", f"{ivlfile}"]
            if metadatatable:
                arguments += ["--metadatatable", f"{metadatatable}"]
            if repmask:
                arguments += ["--repmask", f"{repmask}"]
            if debug:
                arguments += ["--debug"]
            logging.debug(f"Running the comand {' '.join(arguments)} > {name_log_file} &")
            f_log = open(name_log_file, "w")
            subprocess.Popen(arguments, stdout=f_log, stderr=subprocess.STDOUT)
            n += 1
            if n >= number:
                logging.debug("Done!")
                sys.exit()
            time.sleep(wait)  # seconds
        else:
            logging.debug(f"Skipping {sample_id} appears already running or run.")
