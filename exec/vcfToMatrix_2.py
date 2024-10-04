import argparse
from pysam import VariantFile
import pandas as pd
import numpy as np
from scipy.cluster.hierarchy import linkage, dendrogram
from scipy.spatial.distance import pdist
import matplotlib.pyplot as plt
import multiprocessing

#np.seterr(invalid='ignore')

parser = argparse.ArgumentParser(description='Process some integers.')
parser.add_argument('--bcf',
                    type = str,
                    default=  '/gpfs0/scratch/mvc002/testMouse/merged_B6_Balb_10.bcf',
                    help = 'BCF file with multiple samples as columns')
parser.add_argument('--out_base',
                    '-o',
                    type = str,
                    default = 'out_dist',
                    help = 'output tsv file name')
parser.add_argument('--min_snvs_for_sample',
                    type = int,
                    default = 500,
                    help = 'minimum number of SNVs for a sample to be included')
parser.add_argument('--max_prop_missing',
                    type = float,
                    default = 0.9,
                    help = 'max proportion of missing data allowed at a single locus')
parser.add_argument('--verbose',
                    action = 'store_true',
                    help = 'print out extra information')
parser.add_argument('--trim_path',
                    action = 'store_true',
                    help = 'trim path from sample names')
parser.add_argument('--processes',
                    '-p',
                    type = int,
                    default = 1,
                    help = 'number of processes to use for parallel processing')

args = parser.parse_args()

################################################################################
### Code

def main():
    differences, samples = get_diff_matrix_from_bcf(
    bcf_in = args.bcf,
    min_snvs_for_sample = args.min_snvs_for_sample,
    max_prop_missing = args.max_prop_missing)
    # test
    # differences, samples = get_diff_matrix_from_bcf(
    #     #'/gpfs0/scratch/mvc002/testMouse/six_merged.bcf',
    #     500,
    #     0.9)

    prop_diff_matrix = calc_proportion_dist_matrix(differences)

    hclust_out = hierarchical_clustering(prop_diff_matrix)

    og_clusters = get_cluster_members(hclust_out, len(samples))
    #dendrogram(hclust_out)

    #dend_plot = dendrogram(hclust_out, labels=samples)
    # Save the dendrogram plot
    plt.savefig(args.out_base + '_dendrogram.png')

    # Do bootstrapping
    # import time
    # start_time = time.time()
    bootstrap_clusters = get_bootstrap_cluster_members(
        differences,
        n_bootstrap=100,
        threads = 40)
    # end_time = time.time()
    # Looks to be ~50G per thread
    # 35 seconds - 10 bootstrap samples, 30 threads
    # 341 seconds - 100 bootstrap samples, 30 threads
    # 380 seconds - 100 bootstrap samples, 40 threads
    # 45 seconds - 100 bootstrap samples, 40 threads - 0.9 max prop missing - only about 10G ram
    # 377 seconds - 1000 bootstrap samples, 40 threads - 0.9 max prop missing - only about 10G ram
    # 387 seconds - 1000 bootstrap samples, 40 threads - 0.9 max prop missing - only about 10G ram

    # elapsed_time = end_time - start_time

    # print(f"Elapsed time: {elapsed_time} seconds")

    bootstrap_values = calculate_bootstrap_values(og_clusters, bootstrap_clusters)

    plot_dendro_with_bootstrap_values(hclust_out, bootstrap_values, samples)
    plt.savefig(args.out_base + '_dendrogram.png')

    collapsed_clusters = collapse_clusters(og_clusters,
                                        bootstrap_values,
                                        threshold=0.95)

    clusters_with_names = [[str(samples[x]) for x in cluster] for cluster in collapsed_clusters]

    return(clusters_with_names)


########
### functions

####### Can I clear out genotypes with all missing data? #################
def get_diff_matrix_from_bcf(bcf_file,
                             min_snvs_for_sample,
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
    # I should filter out variant positions seen in less than x% of samples
    percent_missing = np.sum(np.isnan(genotype_matrix), axis=1) / len(samples)
    genotype_matrix = genotype_matrix[percent_missing <= max_prop_missing]
    differences = np.abs(genotype_matrix[:, :, np.newaxis] 
                         - genotype_matrix[:, np.newaxis, :])
    differences, samples = filter_diff_matrix(differences,
                                              samples,
                                              min_snvs_for_sample)
    return differences, samples

def pad_len_1_genotype(gt):
    if len(gt) == 1:
        return (gt[0], 0)
    else:
        return gt

def filter_diff_matrix(differences, samples, min_snvs_for_sample):
    n_snps_per_sample = np.sum(~np.isnan(differences), axis=0).diagonal()
    samples_to_keep = np.where(n_snps_per_sample >= min_snvs_for_sample)[0]
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
                            distance_metric='euclidean',
                            linkage_method='ward'):
    distance_matrix = pdist(distance_matrix, metric=distance_metric)
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

def bootstrap_worker(rand_seed):
    np.random.seed(rand_seed)
    return get_one_bootstrap_cluster_members(differences)

def get_bootstrap_cluster_members(differences, n_bootstrap = 10, threads = 1):
    # bootstrap_clusters = []
    with multiprocessing.Pool(processes=threads) as pool:
        bootstrap_clusters = pool.map(bootstrap_worker, range(n_bootstrap))
    # for _ in range(n_bootstrap):
    #     bootstrap_clusters.append(get_one_bootstrap_cluster_members(differences))
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
            if cluster in true_clusters:
                counts[true_clusters.index(cluster)] += 1
    counts /= len(bootstrap_clusters)
    return counts

def plot_dendro_with_bootstrap_values(hclust, bootstrap_values, samples):
    dend_plot = dendrogram(hclust, labels=samples)
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
        #if node_id in cluster_support:
        support = bootstrap_values[node_id] * 100 # original data is proportion
        plt.text(x, y, f'{support:.2f}%', va='bottom', ha='center', fontsize=6)
    plt.show()

def collapse_clusters(true_clusters, bootstrap_values, threshold = 0.95):
    parent_dict = {'none': 'not a cluster'}
    # loop over each cluster, starting with the largest
    for cluster_num in range(len(bootstrap_values) - 1, -1, -1):
        parent_is_cluster = is_parent_a_cluster(parent_dict, cluster_num, true_clusters)
        my_bootstrap = bootstrap_values[cluster_num]
        if (my_bootstrap > threshold) and not parent_is_cluster:
            parent_dict[cluster_num] = 'not a cluster'
        elif (my_bootstrap > threshold) and parent_is_cluster:
            parent_dict[cluster_num] = parent_dict[find_parent_node(cluster_num, true_clusters)]
        elif (my_bootstrap < threshold) and not parent_is_cluster:
            parent_dict[cluster_num] = cluster_num
        elif (my_bootstrap < threshold) and parent_is_cluster:
            parent_dict[cluster_num] = parent_dict[find_parent_node(cluster_num, true_clusters)]
        else:
            raise ValueError(f"Cluster {cluster_num} did not meet any criteria. This shouldn't be possible but here we are")
    clusters = set(parent_dict.values())
    clusters.remove('not a cluster')
    return [true_clusters[x] for x in clusters]

def find_parent_node(node_id, true_clusters):
    for i in range(node_id + 1, len(true_clusters)):
        if true_clusters[node_id][0] in true_clusters[i]:
            return i
    return 'none'

def is_parent_a_cluster(parent_dict, node_id, true_clusters):
    parent_node = find_parent_node(node_id, true_clusters)
    if parent_node in parent_dict:
        if parent_dict[parent_node] != 'not a cluster' and parent_node != 'none':
            return True
        else:
            return False
    else:
        raise ValueError(f"Parent node {parent_node} not found in parent_dict")


################################################################################
### main

if __name__ == '__main__':
    sample_clusters = main()
    for i in range(len(sample_clusters)):
        for sample in sample_clusters[i]:
            print(f"{sample}\t{i}")
