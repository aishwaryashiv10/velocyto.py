import sys
import os
import glob
import re
import gzip
import array
import loompy
import numpy as np
import random
import string
import csv
from collections import defaultdict
import logging
import h5py
from typing import *
import velocyto as vcy

logging.basicConfig(stream=sys.stdout, format='%(asctime)s - %(levelname)s - %(message)s', level=logging.DEBUG)


def id_generator(size: int=6, chars: str=string.ascii_uppercase + string.digits) -> str:
    return ''.join(random.choice(chars) for _ in range(size))


def _run(bamfile: str, ivlfile: str,
         bcfile: str, outputfolder: str,
         sampleid: str, metadatatable: str,
         repmask: str, debug: bool,
         additional_ca: dict={}) -> None:
    """Runs the velocity analysis outputing a loom file

    BAMFILE bam file with sorted reads

    IVLFILE text file generated by velocyto extract_intervals
    """
    
    split_sam_flag = debug

    if sampleid is None:
        assert metadatatable is None, "Cannot fetch sample metadata without valid sampleid"
        sampleid = f'{os.path.basename(bamfile).split(".")[0]}_{id_generator(5)}'
        logging.debug(f"No SAMPLEID specified, the sample will be called {sampleid}")

    # Create an output folder inside the cell ranger output folder
    if outputfolder is None:
        outputfolder = os.path.join(os.path.split(bamfile)[0], "velocyto")
        logging.debug(f"No OUTPUTFOLDER specified, find output files inside {outputfolder}")
    if not os.path.exists(outputfolder):
        os.mkdir(outputfolder)

    if bcfile is None:
        logging.debug("Cell barcodes will be determined while reading the .bam file")
    else:
        # Get valid cell barcodes
        valid_bcs_list = [l.strip() for l in open(bcfile).readlines()]
        valid_cellid_list = np.array([f"{sampleid}:{v_bc}" for v_bc in valid_bcs_list])  # with sample id and with -1
        valid_bcs2idx = dict((bc.split('-')[0], n) for n, bc in enumerate(valid_bcs_list))  # without -1
        logging.debug(f"Read {len(valid_bcs_list)} cell barcodes from {bcfile}")
        logging.debug(f"Example of barcode: {valid_bcs_list[0].split('-')[0]} and cell_id: {valid_cellid_list[0]}")
        
    # Get metadata from sample sheet
    if metadatatable:
        try:
            sample_metadata = vcy.MetadataCollection(metadatatable)
            sample = sample_metadata.where("SampleID", sampleid)
            if len(sample) == 0:
                logging.error(f"Sample ID {sampleid} not found in sample sheet")
                # schema = []  # type: List
                sample = {}
            elif len(sample) > 1:
                logging.error(f"Sample ID {sampleid} has multiple lines in sample sheet")
                sys.exit(1)
            else:
                # schema = sample[0].types
                sample = sample[0].dict
            logging.debug(f"Collecting column attributes from {metadatatable}")
        except (NameError, TypeError) as e:
            logging.warn("SAMPLEFILE was not specified. add -s SAMPLEFILE to add metadata.")
            sample = {}
    else:
        sample = {}

    # Initialize Exon-Intron Counter with the valid barcodes
    exincounter = vcy.ExInCounter(valid_bcs2idx)

    # Load the Intervals definition from file
    n = exincounter.read_genes(ivlfile)
    logging.debug(f"Read {n} intervals for {len(exincounter.genes)} genes from {ivlfile}")

    if repmask is not None:
        m = exincounter.read_repeats(repmask)
        logging.debug(f"Read {m} repeat intervals to mask from {repmask}")

    # Go through the sam files a first time to markup introns
    logging.debug("Marking up introns...")
    exincounter.mark_up_introns(bamfile)

    # Do the actual counting
    if split_sam_flag:
        logging.debug("Counting molecules and writing sam outputs...")
        # NOTE: I should write bam file directly using pysam
        f_sure_introns = open(os.path.join(outputfolder, f"{sampleid}_sure_introns.sam"), "w")
        f_sure_exon = open(os.path.join(outputfolder, f"{sampleid}_sure_exon.sam"), "w")
        f_maybe_exon = open(os.path.join(outputfolder, f"{sampleid}_maybe_exon.sam"), "w")
        f_others = open(os.path.join(outputfolder, f"{sampleid}_not_exon_not_intron.sam"), "w")
        f_chimera = open(os.path.join(outputfolder, f"{sampleid}_chimeras.sam"), "w")
        exincounter.count(bamfile, sam_output=(f_sure_introns, f_sure_exon, f_maybe_exon, f_others, f_chimera))
        f_sure_introns.close()
        f_sure_exon.close()
        f_maybe_exon.close()
        f_others.close()
    else:
        logging.debug("Counting molecules...")
        exincounter.count(bamfile)  # NOTE: we would avoid some millions of if statements evalution if we write two function count and count_with output

    if not exincounter.filter_mode:
        valid_bcs2idx = exincounter.valid_bcs2idx  # without -1
        valid_bcs_list = list(zip(*sorted([(v, k) for k, v in valid_bcs2idx.items()])))[1]  # without -1
        valid_cellid_list = np.array([f"{sampleid}:{v_bc}-1" for v_bc in valid_bcs_list])  # with sampleid and with -1
        logging.debug(f"Example of barcode: {valid_bcs_list[0]} and cell_id: {valid_cellid_list[0]}")
    
    ca = {"CellID": np.array(valid_cellid_list)}
    ca.update(additional_ca)

    for key, value in sample.items():
        ca[key] = np.array([value] * len(valid_cellid_list))
    
    # Save 3' junction/exon read counts
    # NOTE: Legacy code this should be added, where is not redunddant to the newer mapstats.hdf5 file
    logging.debug("Save 3' junction/exon read counts")
    olastexon_counts_file = os.path.join(outputfolder, "lastexon_counts.tab")
    ofd = open(olastexon_counts_file, 'w')
    ofd.write("GeneMame\tGeneID\tAnnotatedTrEnd\tDeducedTrEnd\tLastExonLen\tLastJunctionCount\tLastExonCount\tFromEndReadProfile(3'=>5')...\n")
    for g in exincounter.genes:
        lastjunction_count, lastexon_count = g.get_lastexon_counts()
        lastexon_length = g.get_lastexon_length()
        profile = []
        for c in g.read_start_counts_from_locus_end[:lastexon_length]:
            profile.append(c)
        profile_str = "\t".join([str(c) for c in profile])
        ofd.write("%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" % (g.genename, g.geneid, g.get_tr_end(), g.get_deduced_tr_end(),
                                                        lastexon_length, lastjunction_count, lastexon_count, profile_str))
    ofd.close()

    # Save some stats about exon and introns in a loom file
    logging.debug("Collecting genes structural info statistics")
    
    # Create hdf5 containg the structural stats
    statsfilename = os.path.join(outputfolder, f"{sampleid}_mapstats.hdf5")
    stats_hdf5 = h5py.File(statsfilename, 'w')
    for i, g in enumerate(exincounter.genes):
        # create a group with the Accession Name
        grp = stats_hdf5.create_group(g.geneid)
        type_intervals = np.zeros(len(g.ivls), dtype="|S3")  # not redundand because intron markup is library dependent
        len_intervals = np.zeros(len(g.ivls), dtype="uint32")  # NOTE having this entry to the file is redundant
        valid_intron = np.zeros(len(g.ivls), dtype="bool")
        for j, ivl in enumerate(g.ivls):
            type_intervals[j] = ivl.ivltype
            len_intervals[j] = np.abs(ivl.end - ivl.start)
            valid_intron[j] = ivl.is_sure_valid_intron
        
        grp.create_dataset("reads_per_ivl", data=np.row_stack((g.ivljunction5_read_counts,
                                                               g.ivlinside_read_counts,
                                                               g.ivljunction3_read_counts)))
        grp.create_dataset("ivls_type", data=type_intervals)
        grp.create_dataset("ivls_len", data=len_intervals)
        grp.create_dataset("valid_intron", data=valid_intron)

    stats_hdf5.close()
    logging.debug(f"Mapping statistics have been saved to {statsfilename}")

    # Save to loom file
    outfile = os.path.join(outputfolder, f"{sampleid}.loom")
    logging.debug(f"Generating output file {outfile}")
    
    # row attributes
    atr_table = (("Gene", "genename", str),
                 ("Accession", "geneid", str),
                 ("Chromosome", "chrom", str),
                 ("Strand", "strand", str),
                 ("Start", "start", int),
                 ("End", "end", int))

    logging.debug("Collecting row attributes")
    ra = {}
    for name_col_attr, name_obj_attr, dtyp in atr_table:
        tmp_array = np.zeros((len(exincounter.genes),), dtype=object)  # type: np.ndarray
        for i, g in enumerate(exincounter.genes):
            tmp_array[i] = getattr(g, name_obj_attr)
        ra[name_col_attr] = tmp_array.astype(dtyp)
    
    logging.debug("Generating data table")
    shape_loom = len(exincounter.genes), len(valid_bcs_list)
    spliced = np.zeros(shape_loom, dtype=vcy.LOOM_NUMERIC_DTYPE)
    unspliced = np.zeros(shape_loom, dtype=vcy.LOOM_NUMERIC_DTYPE)
    ambiguous = np.zeros(shape_loom, dtype=vcy.LOOM_NUMERIC_DTYPE)
    for i, g in enumerate(exincounter.genes):
        spliced[i, :] = g.spliced_mol_counts
        unspliced[i, :] = g.unspliced_mol_counts
        ambiguous[i, :] = g.ambiguous_mol_counts
    
    total = spliced + unspliced + ambiguous
    if not np.any(total):
        logging.error("The output file is empty check the input!")

    logging.debug("Writing loom file")
    ds = loompy.create(filename=outfile, matrix=total, row_attrs=ra, col_attrs=ca, dtype="float32")
    ds.set_layer(name="spliced", matrix=spliced, dtype=vcy.LOOM_NUMERIC_DTYPE)
    ds.set_layer(name="unspliced", matrix=unspliced, dtype=vcy.LOOM_NUMERIC_DTYPE)
    ds.set_layer(name="ambiguous", matrix=ambiguous, dtype=vcy.LOOM_NUMERIC_DTYPE)
    ds.close()

    logging.debug("Terminated Succesfully!")
