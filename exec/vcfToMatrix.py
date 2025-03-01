import argparse
import sys
from pysam import VariantFile
import numpy as np
from scipy.cluster.hierarchy import linkage, dendrogram, cut_tree
from scipy.spatial.distance import squareform
import matplotlib
matplotlib.use('pdf')
import matplotlib.pyplot as plt
import multiprocessing
from itertools import repeat, compress

parser = argparse.ArgumentParser(description='Process some integers.')
parser.add_argument('--bcf',
                    type = str,
                    default=  '/home/gdrobertslab/lab/Analysis/Matt/24_Osteo_atlas/output/id_tumor/snvs/SJOS013769_D1/mergedSJOS013769_D1_c5.bcf',
                    help = 'BCF file with multiple samples as columns')
parser.add_argument('--figure_file',
                    '-o',
                    type = str,
                    default = 'dendrogram.pdf',
                    help = 'output file name of plot. Id suggest either png or pdf')
parser.add_argument('--min_snvs_for_cluster',
                    type = int,
                    default = 1000,
                    help = 'minimum number of SNVs for a cluster to be included')
parser.add_argument('--max_prop_missing',
                    type = float,
                    default = 0.9,
                    help = 'max proportion of missing data allowed at a single locus')
parser.add_argument('--n_bootstrap',
                    type = int,
                    default = 1000,
                    help = 'number of bootstrap samples to use')
parser.add_argument('--bootstrap_threshold',
                    type = float,
                    default = 0.99,
                    help = 'threshold for collapsing clusters')
parser.add_argument('--verbose',
                    action = 'store_true',
                    help = 'print out extra information')
parser.add_argument('--processes',
                    '-p',
                    type = int,
                    default = 1,
                    help = 'number of processes to use for parallel processing')
parser.add_argument('--fig_width',
                    type = float,
                    default = 6,
                    help = 'width of the figure in inches')
parser.add_argument('--fig_height',
                    type = float,
                    default = 6,
                    help = 'height of the figure in inches')
parser.add_argument('--fig_dpi',
                    type = int,
                    default = 300,
                    help = 'dpi of the figure')

args = parser.parse_args()

################################################################################
### Code

def main():
    differences, samples = get_diff_matrix_from_bcf(
        bcf_file = args.bcf,
        min_snvs_for_cluster = args.min_snvs_for_cluster,
        max_prop_missing = args.max_prop_missing)

    prop_diff_matrix = calc_proportion_dist_matrix(differences)

    hclust_out = hierarchical_clustering(prop_diff_matrix)

    original_clusters = get_cluster_members(hclust_out, len(samples))

    # Do bootstrapping
    bootstrap_clusters = get_bootstrap_cluster_members(
        differences,
        args.n_bootstrap,
        args.processes)

    bootstrap_values = calculate_bootstrap_values(original_clusters,
                                                  bootstrap_clusters)

    plt.figure(figsize=(args.fig_width, args.fig_height))
    plot = plot_dendro_with_bootstrap_values(hclust_out, bootstrap_values, samples)
    plot.tight_layout()
    plot.savefig(args.figure_file, dpi = args.fig_dpi)

    collapsed_clusters = collapse_clusters(original_clusters,
                                           bootstrap_values,
                                           threshold=args.bootstrap_threshold)

    top_lvl_clusters = collapse_top_lvl_clusters(hclust_out,
                                                 bootstrap_values,
                                                 threshold=args.bootstrap_threshold)

    clusters_with_names = [[str(samples[x]) for x in cluster] for cluster in collapsed_clusters]
    top_lvl_clusters_with_names = [[str(samples[x]) for x in cluster] for cluster in top_lvl_clusters]

    print_cluster_names(clusters_with_names, top_lvl_clusters_with_names)

    return()


########
### functions

####### Can I clear out genotypes with all missing data? #################
def get_diff_matrix_from_bcf(bcf_file,
                             min_snvs_for_cluster,
                             max_prop_missing):
    dist_key_dict = {'00':            0,
                     '01':            1,
                     '10':            1,
                     '11':            2,
                     '(None, None)':  np.nan}
    ### Check if bcf index exists
    bcf_in = VariantFile(bcf_file)
    samples = tuple(bcf_in.header.samples)
    records = tuple(x for x in list(bcf_in.fetch()) if (len(x.alts) == 1))
    bcf_in.close()

    # Precompute the genotype tuples for all samples
    genotype_tuples = np.array([
        [tuple(pad_len_1_genotype(rec.samples[sample]['GT'])) for sample in samples]
        for rec in records
    ])

    # Convert genotype tuples to strings and look up in dist_key_dict
    genotype_matrix = np.array([
        [dist_key_dict.get(''.join(map(str, gt)), np.nan) for gt in sample_genotypes]
        for sample_genotypes in genotype_tuples
    ])

    # Filter out variant positions seen in less than x% of samples
    percent_missing = np.sum(np.isnan(genotype_matrix), axis=1) / len(samples)
    genotype_matrix = genotype_matrix[percent_missing <= max_prop_missing]
    differences = np.abs(genotype_matrix[:, :, np.newaxis]
                         - genotype_matrix[:, np.newaxis, :])
    differences, samples = filter_diff_matrix(differences,
                                              samples,
                                              min_snvs_for_cluster)

    if differences.shape[1] == 0:
        sys.exit(
            f'No clusters with at least {min_snvs_for_cluster} SNVs for {args.bcf}'
        )

    return differences, samples

def pad_len_1_genotype(gt):
    if len(gt) == 1:
        return (gt[0], 0)
    else:
        return gt

def filter_diff_matrix(differences, samples, min_snvs_for_cluster):
    n_snps_per_sample = np.sum(~np.isnan(differences), axis=0).diagonal()
    samples_to_keep = np.where(n_snps_per_sample >= min_snvs_for_cluster)[0]
    samples = np.array(samples)[samples_to_keep]
    differences = differences[:, samples_to_keep, :]
    differences = differences[:, :, samples_to_keep]
    return differences, samples

def calc_proportion_dist_matrix(differences, bootstrap=False):
    if bootstrap:
        differences = differences[np.random.choice(differences.shape[0],
                                                   size=differences.shape[0],
                                                   replace=True)]
    n_comps_matrix = np.sum(~np.isnan(differences), axis=0)
    # Sum up differences while ignoring np.nan values
    sum_differences = np.nansum(differences, axis=0)
    # Calculate the proportion of differences
    prop_diff_matrix = sum_differences / (n_comps_matrix * 2)
    return prop_diff_matrix

def hierarchical_clustering(distance_matrix,
                            linkage_method='ward'):
    distance_matrix = squareform(distance_matrix)
    Z = linkage(distance_matrix, method=linkage_method)
    return Z

def get_cluster_members(hclust, n_samples):
    cluster_members_by_id = [[x] for x in range(n_samples)]
    for i in range(hclust.shape[0]):
        cluster_ids = hclust[i, :2].astype(int).tolist()
        all_members = list()
        for this_cluster in cluster_ids:
            junk = [all_members.append(x) for x in cluster_members_by_id[this_cluster]]
        all_members.sort()
        cluster_members_by_id.append(all_members)
    return cluster_members_by_id

def bootstrap_worker(rand_seed, differences):
    np.random.seed(rand_seed)
    return get_one_bootstrap_cluster_members(differences)

def get_bootstrap_cluster_members(differences,
                                  n_bootstrap=1000,
                                  threads=1):
    with multiprocessing.Pool(processes=threads) as pool:
        bootstrap_clusters = pool.starmap(bootstrap_worker,
                                          zip(range(n_bootstrap),
                                              repeat(differences)))
    return bootstrap_clusters

def get_one_bootstrap_cluster_members(differences):
    bootstrap_z = hierarchical_clustering(
            calc_proportion_dist_matrix(differences, True))
    these_clusters = get_cluster_members(bootstrap_z, differences.shape[1])
    return these_clusters

# Calculate bootstrap values for each node
def calculate_bootstrap_values(true_clusters, bootstrap_clusters):
    counts = np.zeros(len(true_clusters))
    for one_bootstrap in bootstrap_clusters:
        for cluster in one_bootstrap:
            # We don't want "clusters" of size 1 to have bootstrap values of 1
            if cluster in true_clusters and len(cluster) > 1:
                counts[true_clusters.index(cluster)] += 1
    counts /= len(bootstrap_clusters)
    return counts

def plot_dendro_with_bootstrap_values(hclust, bootstrap_values, samples):
    dend_plot = dendrogram(hclust,
                           labels=samples,
                           leaf_rotation=90.,
                           leaf_font_size=6.,
                           color_threshold=0)
    icoords = dend_plot['icoord']
    dcoords = dend_plot['dcoord']
    # Need to sort the coordinates so that they match the order of the nodes
    # This sorts the coordinates by the y value
    right_order = np.argsort([x[1] for x in dcoords])
    icoords = [icoords[x] for x in right_order]
    dcoords = [dcoords[x] for x in right_order]
    leaf_labels = dend_plot['ivl']
    n_samples = len(leaf_labels)
    node_indices = list(range(n_samples, n_samples + len(icoords)))
    for i, (icoord, dcoord) in enumerate(zip(icoords, dcoords)):
        x = ((icoord[1] + icoord[2]) * 0.5)
        y = dcoord[1]
        node_id = node_indices[i]
        support = bootstrap_values[node_id] * 100 # original data is proportion
        plt.text(x, y, f'{support:.2f}%', va='bottom', ha='center', fontsize=6)
    return(plt)

def collapse_clusters(true_clusters,
                      bootstrap_values,
                      threshold = args.bootstrap_threshold):
    parent_dict = {'none': 'fork'}
    # loop over each cluster, starting with the largest
    for cluster_num in range(len(bootstrap_values) - 1, -1, -1):
        parent_is_fork = is_parent_a_fork(parent_dict,
                                          cluster_num,
                                          true_clusters)
        my_bootstrap = bootstrap_values[cluster_num]
        if parent_is_fork:
            if (my_bootstrap >= threshold):
                parent_dict[cluster_num] = 'fork'
            elif (my_bootstrap < threshold):
                parent_dict[cluster_num] = cluster_num
        else:
            parent_dict[cluster_num] = parent_dict[find_parent_node(cluster_num, true_clusters)]
    # Keep only keys from "cluster" nodes in parent_dict
    clusters = set(parent_dict.values())
    clusters.remove('fork')
    return [true_clusters[x] for x in clusters]

def collapse_top_lvl_clusters(hclust,
                              bootstrap_values,
                              threshold = args.bootstrap_threshold):
    top_lvl = len(bootstrap_values) - 1
    # top_lvl bootstrap value is the first division
    if bootstrap_values[top_lvl] >= threshold:
        split_clusters = cut_tree(hclust, n_clusters=2).reshape(-1)

        output_list = [[], []]
        output_list[0] = list(
            compress(
                list(range(len(split_clusters))),
                split_clusters == 0
                )
            )

        output_list[1] = list(
            compress(
                list(range(len(split_clusters))),
                split_clusters == 1
                )
            )
    else:
        output_list = [list(range(len(bootstrap_values)))]

    return output_list

def find_parent_node(node_id, true_clusters):
    for i in range(node_id + 1, len(true_clusters)):
        if true_clusters[node_id][0] in true_clusters[i]:
            return i
    return 'none'

def is_parent_a_fork(parent_dict, node_id, true_clusters):
    parent_node = find_parent_node(node_id, true_clusters)
    if parent_node in parent_dict:
        if parent_dict[parent_node] == 'fork':
            return True
        else:
            return False
    else:
        raise ValueError(f"Parent node {parent_node} not found in parent_dict")

def print_cluster_names(clusters_with_names, top_lvl_clusters_with_names):
    cluster_name_dict = {}
    # We're going to put both datasets into the same dictionary using the sample
    # name as the key with sub-dictionaries for all_groups and top_lvl_groups
    # Then we will loop over the dictionary to print out the group names for
    # each cluster name
    for i in range(len(clusters_with_names)):
        for sample in clusters_with_names[i]:
            cluster_name_dict[sample] = {'all_groups': 'group' + str(i)}

    for i in range(len(top_lvl_clusters_with_names)):
        for sample in top_lvl_clusters_with_names[i]:
            cluster_name_dict[sample]['top_lvl_groups'] = 'top_lvl_group' + str(i)

    for this_key in cluster_name_dict.keys():
        print(f'{this_key}\t{cluster_name_dict[this_key]["all_groups"]}\t{cluster_name_dict[this_key]["top_lvl_groups"]}')

################################################################################
### main

if __name__ == '__main__':
    main()
