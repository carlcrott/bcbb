"""Handle running, parsing and manipulating metrics available through Picard.
"""
import os
import glob
import json
import contextlib
import pprint

from bcbio.utils import tmpfile, file_exists
from bcbio.distributed.transaction import file_transaction

import pysam

class PicardMetricsParser:
    """Read metrics files produced by Picard analyses.

    Metrics info:
    http://www.broadinstitute.org/~prodinfo/picard_metric_definitions.html
    """
    def __init__(self):
        pass

    def get_summary_metrics(self, align_metrics, dup_metrics,
            insert_metrics=None, hybrid_metrics=None, vrn_vals=None):
        """Retrieve a high level summary of interesting metrics.
        """
        with open(align_metrics) as in_handle:
            align_vals = self._parse_align_metrics(in_handle)
        with open(dup_metrics) as in_handle:
            dup_vals = self._parse_dup_metrics(in_handle)
        (insert_vals, hybrid_vals) = (None, None)
        if insert_metrics and os.path.exists(insert_metrics):
            with open(insert_metrics) as in_handle:
                insert_vals = self._parse_insert_metrics(in_handle)
        if hybrid_metrics and os.path.exists(hybrid_metrics):
            with open(hybrid_metrics) as in_handle:
                hybrid_vals = self._parse_hybrid_metrics(in_handle)

        return self._tabularize_metrics(align_vals, dup_vals, insert_vals,
                hybrid_vals, vrn_vals)

    def extract_metrics(self, metrics_files):
        """Return summary information for a lane of metrics files.
        """
        extension_maps = dict(
                align_metrics = (self._parse_align_metrics, "AL"),
                dup_metrics = (self._parse_dup_metrics, "DUP"),
                hs_metrics = (self._parse_hybrid_metrics, "HS"),
                insert_metrics = (self._parse_insert_metrics, "INS"),
                )
        all_metrics = dict()
        for fname in metrics_files:
            ext = os.path.splitext(fname)[-1][1:]
            try:
                parse_fn, prefix = extension_maps[ext]
            except KeyError:
                parse_fn = None
            if parse_fn:
                with open(fname) as in_handle:
                    for key, val in parse_fn(in_handle).iteritems():
                        if not key.startswith(prefix):
                            key = "%s_%s" % (prefix, key)
                        all_metrics[key] = val
        return all_metrics

    def _tabularize_metrics(self, align_vals, dup_vals, insert_vals,
            hybrid_vals, vrn_vals):
        out = []
        # handle high level alignment for paired values
        paired = insert_vals is not None


        total = align_vals["TOTAL_READS"]
        dup_total = int(dup_vals["READ_PAIRS_EXAMINED"])
        align_total = int(align_vals["PF_READS_ALIGNED"])
        out.append(("Total", _add_commas(str(total)),
            ("paired" if paired else "")))
        out.append(self._count_percent("Aligned",
            align_vals["PF_READS_ALIGNED"], total))
        if paired:
            out.append(self._count_percent("Pairs aligned",
                align_vals["READS_ALIGNED_IN_PAIRS"], total))
            align_total = int(align_vals["READS_ALIGNED_IN_PAIRS"])
            if align_total != dup_total:
                out.append(("Alignment combinations", _add_commas(str(dup_total)),
                    ""))
            out.append(self._count_percent("Pair duplicates",
                dup_vals["READ_PAIR_DUPLICATES"], dup_total))
            std = insert_vals.get("STANDARD_DEVIATION", "?")
            std_dev = "+/- %.1f" % float(std.replace(",", ".")) if (std and std != "?") else ""
            out.append(("Insert size",
                "%.1f" % float(insert_vals["MEAN_INSERT_SIZE"].replace(",", ".")), std_dev))
        if hybrid_vals:
            out.append((None, None, None))
            out.extend(self._tabularize_hybrid(hybrid_vals))
        if vrn_vals:
            out.append((None, None, None))
            out.extend(self._tabularize_variant(vrn_vals))
        return out

    def _tabularize_variant(self, vrn_vals):
        out = []
        out.append(("Total variations", vrn_vals["total"], ""))
        out.append(("In dbSNP", "%.1f\%%" % vrn_vals["dbsnp_pct"], ""))
        out.append(("Transition/Transversion (all)", "%.2f" %
            vrn_vals["titv_all"], ""))
        out.append(("Transition/Transversion (dbSNP)", "%.2f" %
            vrn_vals["titv_dbsnp"], ""))
        out.append(("Transition/Transversion (novel)", "%.2f" %
            vrn_vals["titv_novel"], ""))
        return out

    def _tabularize_hybrid(self, hybrid_vals):
        out = []
        total = hybrid_vals["PF_UQ_BASES_ALIGNED"]
        out.append(self._count_percent("On bait bases",
            hybrid_vals["ON_BAIT_BASES"], total))
        out.append(self._count_percent("Near bait bases",
            hybrid_vals["NEAR_BAIT_BASES"], total))
        out.append(self._count_percent("Off bait bases",
            hybrid_vals["OFF_BAIT_BASES"], total))
        out.append(("Mean bait coverage", "%.1f" %
            float(hybrid_vals["MEAN_BAIT_COVERAGE"]), ""))
        out.append(self._count_percent("On target bases",
            hybrid_vals["ON_TARGET_BASES"], total))
        out.append(("Mean target coverage", "%dx" %
            float(hybrid_vals["MEAN_TARGET_COVERAGE"]), ""))
        out.append(("10x coverage targets", "%.1f\%%" %
            (float(hybrid_vals["PCT_TARGET_BASES_10X"]) * 100.0), ""))
        out.append(("Zero coverage targets", "%.1f\%%" %
            (float(hybrid_vals["ZERO_CVG_TARGETS_PCT"]) * 100.0), ""))
        out.append(("Fold enrichment", "%dx" %
            float(hybrid_vals["FOLD_ENRICHMENT"]), ""))
        return out

    def _count_percent(self, text, count, total):
        if float(total) > 0:
            percent = "(%.1f\%%)" % (float(count) / float(total) * 100.0)
        else:
            percent = ""
        return (text, _add_commas(str(count)), percent)

    def _parse_hybrid_metrics(self, in_handle):
        want_stats = ["PF_UQ_BASES_ALIGNED", "ON_BAIT_BASES",
                "NEAR_BAIT_BASES", "OFF_BAIT_BASES",
                "ON_TARGET_BASES",
                "MEAN_BAIT_COVERAGE",
                "MEAN_TARGET_COVERAGE",
                "FOLD_ENRICHMENT",
                "ZERO_CVG_TARGETS_PCT",
                "BAIT_SET",
                "GENOME_SIZE",
                "HS_LIBRARY_SIZE",
                "BAIT_TERRITORY",
                "TARGET_TERRITORY",
                "PCT_SELECTED_BASES",
                "FOLD_80_BASE_PENALTY",
                "PCT_TARGET_BASES_2X",
                "PCT_TARGET_BASES_10X",
                "PCT_TARGET_BASES_20X",
                "HS_PENALTY_20X"
                ]
        header = self._read_off_header(in_handle)
        info = in_handle.readline().rstrip("\n").split("\t")
        vals = self._read_vals_of_interest(want_stats, header, info)
        return vals

    def _parse_align_metrics(self, in_handle):
        half_stats = ["TOTAL_READS", "PF_READS_ALIGNED",
                "READS_ALIGNED_IN_PAIRS"]
        std_stats = ["PF_HQ_ALIGNED_Q20_BASES",
                "PCT_READS_ALIGNED_IN_PAIRS", "MEAN_READ_LENGTH"]
        want_stats = half_stats + std_stats
        header = self._read_off_header(in_handle)
        while 1:
            info = in_handle.readline().rstrip("\n").split("\t")
            if len(info) <= 1:
                break
            vals = self._read_vals_of_interest(want_stats, header, info)
            if info[0].lower() == "pair":
                new_vals = dict()
                for item, val in vals.iteritems():
                    if item in half_stats:
                        new_vals[item] = str(int(val) // 2)
                    else:
                        new_vals[item] = val
                vals = new_vals
        return vals

    def _parse_dup_metrics(self, in_handle):
        want_stats = ["READ_PAIRS_EXAMINED", "READ_PAIR_DUPLICATES",
                "PERCENT_DUPLICATION", "ESTIMATED_LIBRARY_SIZE"]
        header = self._read_off_header(in_handle)
        info = in_handle.readline().rstrip("\n").split("\t")
        vals = self._read_vals_of_interest(want_stats, header, info)
        return vals

    def _parse_insert_metrics(self, in_handle):
        want_stats = ["MEDIAN_INSERT_SIZE", "MIN_INSERT_SIZE",
                "MAX_INSERT_SIZE", "MEAN_INSERT_SIZE", "STANDARD_DEVIATION"]
        header = self._read_off_header(in_handle)
        info = in_handle.readline().rstrip("\n").split("\t")
        vals = self._read_vals_of_interest(want_stats, header, info)
        return vals

    def _read_vals_of_interest(self, want, header, info):
        want_indexes = [header.index(w) for w in want]
        vals = dict()
        for i in want_indexes:
            vals[header[i]] = info[i]
        return vals

    def _read_off_header(self, in_handle):
        while 1:
            line = in_handle.readline()
            if line.startswith("## METRICS"):
                break
        return in_handle.readline().rstrip("\n").split("\t")

class PicardMetrics:
    """Run reports using Picard, returning parsed metrics and files.
    """
    def __init__(self, picard, tmp_dir):
        self._picard = picard
        self._tmp_dir = tmp_dir
        self._parser = PicardMetricsParser()

    def report(self, align_bam, ref_file, is_paired, bait_file, target_file):
        """Produce report metrics using Picard with sorted aligned BAM file.
        """
        dup_bam, dup_metrics = self._get_current_dup_metrics(align_bam)
        align_metrics = self._collect_align_metrics(dup_bam, ref_file)
        # Prefer the GC metrics in FastQC instead of Picard
        # gc_graph, gc_metrics = self._gc_bias(dup_bam, ref_file)
        gc_graph = None
        insert_graph, insert_metrics, hybrid_metrics = (None, None, None)
        if is_paired:
            insert_graph, insert_metrics = self._insert_sizes(dup_bam)
        if bait_file and target_file:
            hybrid_metrics = self._hybrid_select_metrics(
                    dup_bam, bait_file, target_file)
        vrn_vals = self._variant_eval_metrics(dup_bam)
        summary_info = self._parser.get_summary_metrics(align_metrics,
                dup_metrics, insert_metrics, hybrid_metrics,
                vrn_vals)
        pprint.pprint(summary_info)
        graphs = []
        if gc_graph and os.path.exists(gc_graph):
            graphs.append((gc_graph, "Distribution of GC content across reads"))
        if insert_graph and os.path.exists(insert_graph):
            graphs.append((insert_graph, "Distribution of paired end insert sizes"))
        return summary_info, graphs

    def _get_current_dup_metrics(self, align_bam):
        """Retrieve existing duplication metrics file, or generate if not present.
        """
        dup_fname_pos = align_bam.find("-dup")
        if dup_fname_pos > 0:
            base_name = align_bam[:dup_fname_pos]
            metrics = glob.glob("{0}*.dup_metrics".format(base_name))
            assert len(metrics) > 0, "Appear to have deduplication but did not find metrics file"
            return align_bam, metrics[0]
        else:
            return self._picard.run_fn("picard_mark_duplicates", align_bam)

    def _check_metrics_file(self, bam_name, metrics_ext):
        """Check for an existing metrics file for the given BAM.
        """
        base, _ = os.path.splitext(bam_name)
        try:
            int(base[-1])
            can_glob = False
        except ValueError:
            can_glob = True
        check_fname = "{base}{maybe_glob}.{ext}".format(
            base=base, maybe_glob="*" if can_glob else "", ext=metrics_ext)
        glob_fnames = glob.glob(check_fname)
        if len(glob_fnames) > 0:
            return glob_fnames[0]
        else:
            return "{base}.{ext}".format(base=base, ext=metrics_ext)

    def _hybrid_select_metrics(self, dup_bam, bait_file, target_file):
        """Generate metrics for hybrid selection efficiency.
        """
        metrics = self._check_metrics_file(dup_bam, "hs_metrics")
        if not file_exists(metrics):
            with bed_to_interval(bait_file, dup_bam) as ready_bait:
                with bed_to_interval(target_file, dup_bam) as ready_target:
                    with file_transaction(metrics) as tx_metrics:
                        opts = [("BAIT_INTERVALS", ready_bait),
                                ("TARGET_INTERVALS", ready_target),
                                ("INPUT", dup_bam),
                                ("OUTPUT", tx_metrics)]
                        self._picard.run("CalculateHsMetrics", opts)
        return metrics

    def _variant_eval_metrics(self, dup_bam):
        """Find metrics for evaluating variant effectiveness.
        """
        base, ext = os.path.splitext(dup_bam)
        end_strip = "-dup"
        base = base[:-len(end_strip)] if base.endswith(end_strip) else base
        mfiles = glob.glob("%s*eval_metrics" % base)
        if len(mfiles) > 0:
            with open(mfiles[0]) as in_handle:
                # pull the metrics as JSON from the last line in the file
                for line in in_handle:
                    pass
                metrics = json.loads(line)
            return metrics
        else:
            return None

    def _gc_bias(self, dup_bam, ref_file):
        gc_metrics = self._check_metrics_file(dup_bam, "gc_metrics")
        gc_graph = "%s-gc.pdf" % os.path.splitext(gc_metrics)[0]
        if not file_exists(gc_metrics):
            with file_transaction(gc_graph, gc_metrics) as \
                     (tx_graph, tx_metrics):
                opts = [("INPUT", dup_bam),
                        ("OUTPUT", tx_metrics),
                        ("CHART", tx_graph),
                        ("R", ref_file)]
                self._picard.run("CollectGcBiasMetrics", opts)
        return gc_graph, gc_metrics

    def _insert_sizes(self, dup_bam):
        insert_metrics = self._check_metrics_file(dup_bam, "insert_metrics")
        insert_graph = "%s-insert.pdf" % os.path.splitext(insert_metrics)[0]
        if not file_exists(insert_metrics):
            with file_transaction(insert_graph, insert_metrics) as \
                     (tx_graph, tx_metrics):
                opts = [("INPUT", dup_bam),
                        ("OUTPUT", tx_metrics),
                        ("H", tx_graph)]
                self._picard.run("CollectInsertSizeMetrics", opts)
        return insert_graph, insert_metrics

    def _collect_align_metrics(self, dup_bam, ref_file):
        align_metrics = self._check_metrics_file(dup_bam, "align_metrics")
        if not file_exists(align_metrics):
            with file_transaction(align_metrics) as tx_metrics:
                opts = [("INPUT", dup_bam),
                        ("OUTPUT", tx_metrics),
                        ("R", ref_file)]
                self._picard.run("CollectAlignmentSummaryMetrics", opts)
        return align_metrics

def _add_commas(s, sep=','):
    """Add commas to output counts.

    From: http://code.activestate.com/recipes/498181
    """
    if len(s) <= 3: return s
    return _add_commas(s[:-3], sep) + sep + s[-3:]

@contextlib.contextmanager
def bed_to_interval(orig_bed, bam_file):
    """Add header and format BED bait and target files for Picard if necessary.
    """
    with open(orig_bed) as in_handle:
        line = in_handle.readline()
    if line.startswith("@"):
        yield orig_bed
    else:
        bam_handle = pysam.Samfile(bam_file, "rb")
        with contextlib.closing(bam_handle):
            header = bam_handle.text
        with tmpfile(dir=os.getcwd(), prefix="picardbed") as tmp_bed:
            with open(tmp_bed, "w") as out_handle:
                out_handle.write(header)
                with open(orig_bed) as in_handle:
                    for line in in_handle:
                        parts = line.rstrip().split("\t")
                        if len(parts) == 3:
                            parts.append("+")
                            parts.append("a")
                        out_handle.write("\t".join(parts) + "\n")
            yield tmp_bed
