#! /g/arendt/EM_6dpf_segmentation/platy-browser-data/software/conda/miniconda3/envs/platybrowser/bin/python

import os
import json
import argparse
from subprocess import check_output
from concurrent import futures

import imageio
import luigi
import numpy as np
import pandas as pd
from pybdv import make_bdv

from scripts.files import copy_release_folder, make_folder_structure, make_bdv_server_file, get_source_names
from scripts.files.xml_utils import get_h5_path_from_xml, write_simple_xml
from scripts.release_helper import add_version
from scripts.extension.registration import ApplyRegistrationLocal, ApplyRegistrationSlurm
from scripts.default_config import get_default_shebang
from scripts.attributes.base_attributes import base_attributes
from scripts.attributes.genes import create_auxiliary_gene_file, write_genes_table
from scripts.util import add_max_id


REGION_NAMES = ('AllGlands',
                'CrypticSegment',
                'Glands',
                'Head',
                'PNS',
                'Pygidium',
                'RestOfAnimal',
                'Stomodeum',
                'VNC',
                'ProSPr6-Ref')


def get_tags(new_tag):
    tag = check_output(['git', 'describe', '--abbrev=0']).decode('utf-8').rstrip('\n')
    if new_tag is None:
        new_tag = tag.split('.')
        new_tag[-1] = str(int(new_tag[-1]) + 1)
        new_tag = '.'.join(new_tag)
    return tag, new_tag


def parse_prospr(prefix, name):
    name = os.path.split(name)[1]
    name = os.path.splitext(name)[0]
    name = name.split('--')[0]
    # TODO split off further extensions here?
    # handle ENR differently, because ENR is not very informative?
    # name = name.split('-')[0]
    if name in REGION_NAMES:
        name = '-'.join([prefix, 'segmented', name])
    else:
        name = '-'.join([prefix, name, 'MED'])
    return name


def copy_file(in_path, out_path, resolution=[.55, .55, .55]):
    if os.path.exists(out_path + '.xml'):
        return
    print("Copy", in_path, "to", out_path)
    vol = np.asarray(imageio.volread(in_path + '-ch0.tif'))
    downscale_factors = [[2, 2, 2], [2, 2, 2], [2, 2, 2]]
    make_bdv(vol, out_path, downscale_factors,
             unit='micrometer', resolution=resolution)


def copy_to_h5(inputs, output_folder):
    print("Copy tifs to bdv/hdf5 in", output_folder)
    outputs = [os.path.join(output_folder, os.path.split(inp)[1]) for inp in inputs]
    n_jobs = 48
    with futures.ProcessPoolExecutor(n_jobs) as tp:
        tasks = [tp.submit(copy_file, inp, outp) for inp, outp in zip(inputs, outputs)]
        [t.result() for t in tasks]


def registration_impl(inputs, outputs, transformation_file, output_folder,
                      tmp_folder, target, max_jobs, interpolation, dtype='unsigned char'):
    task = ApplyRegistrationSlurm if target == 'slurm' else ApplyRegistrationLocal

    # write path name files to json
    input_file = os.path.join(tmp_folder, 'input_files.json')
    inputs = [os.path.abspath(inpath) for inpath in inputs]
    with open(input_file, 'w') as f:
        json.dump(inputs, f)

    output_file = os.path.join(tmp_folder, 'output_files.json')
    outputs = [os.path.abspath(outpath) for outpath in outputs]
    with open(output_file, 'w') as f:
        json.dump(outputs, f)

    # update the task config
    config_dir = os.path.join(tmp_folder, 'configs')
    os.makedirs(config_dir, exist_ok=True)

    shebang = get_default_shebang()
    global_config = task.default_global_config()
    global_config.update({'shebang': shebang})
    with open(os.path.join(config_dir, 'global.config'), 'w') as f:
        json.dump(global_config, f)

    task_config = task.default_task_config()
    task_config.update({'mem_limit': 16, 'time_limit': 240, 'threads_per_job': 4,
                        'ResultImagePixelType': dtype})
    with open(os.path.join(config_dir, 'apply_registration.config'), 'w') as f:
        json.dump(task_config, f)

    t = task(tmp_folder=tmp_folder, config_dir=config_dir, max_jobs=max_jobs,
             input_path_file=input_file, output_path_file=output_file,
             transformation_file=transformation_file, output_format='tif',
             interpolation=interpolation)
    ret = luigi.build([t], local_scheduler=True)
    if not ret:
        raise RuntimeError("Registration failed")

    copy_to_h5(outputs, output_folder)


# TODO expose interpolation mode
def apply_registration(input_folder, new_folder,
                       transformation_file, source_prefix,
                       target, max_jobs, name_parser):
    tmp_folder = './tmp_registration'
    os.makedirs(tmp_folder, exist_ok=True)

    # find all input files
    names = os.listdir(input_folder)
    inputs = [os.path.join(input_folder, name) for name in names]
    # we ignore subfolders, because we might have some special volumes there
    # (e.g. virtual cells for prospr, which are treated as segmentation)
    inputs = [inp for inp in inputs if not os.path.isdir(inp)]

    if len(inputs) == 0:
        raise RuntimeError("Did not find any files with prefix %s in %s" % (source_prefix,
                                                                            input_folder))

    # writing multiple hdf5 files in parallel with the elastix plugin is broken,
    # so we write temporary files to tif instead and copy them to hdf5 with python
    output_folder = os.path.join(tmp_folder, 'outputs')
    os.makedirs(output_folder, exist_ok=True)
    output_names = [name_parser(source_prefix, name) for name in inputs]
    outputs = [os.path.join(output_folder, name) for name in output_names]

    output_folder = os.path.join(new_folder, 'images')
    registration_impl(inputs, outputs, transformation_file, output_folder,
                      tmp_folder, target, max_jobs, interpolation='nearest')


def update_prospr(new_folder, input_folder, transformation_file, target, max_jobs):

    # # update the auxiliaty gene volume
    image_folder = os.path.join(new_folder, 'images')
    aux_out_path = os.path.join(new_folder, 'misc', 'prospr-6dpf-1-whole_meds_all_genes.h5')
    create_auxiliary_gene_file(image_folder, aux_out_path)
    # write the new xml
    h5_path = os.path.split(aux_out_path)[1]
    xml_path = os.path.splitext(aux_out_path)[0] + '.xml'
    write_simple_xml(xml_path, h5_path, path_type='relative')

    # update the gene table
    seg_path = os.path.join(new_folder, 'segmentations', 'sbem-6dpf-1-whole-segmented-cells-labels.xml')
    seg_path = get_h5_path_from_xml(seg_path, return_absolute_path=True)
    assert os.path.exists(seg_path), seg_path

    table_folder = os.path.join(new_folder, 'tables', 'sbem-6dpf-1-whole-segmented-cells-labels')
    default_table_path = os.path.join(table_folder, 'default.csv')
    table = pd.read_csv(default_table_path, sep='\t')
    labels = table['label_id'].values.astype('uint64')

    tmp_folder = './tmp_registration'
    out_path = os.path.join(table_folder, 'genes.csv')
    # we need to remove the link to the old gene table, if it exists
    if os.path.exists(out_path):
        assert os.path.islink(out_path), out_path
        print("Remove link to previous gene table:", out_path)
        os.unlink(out_path)
    write_genes_table(seg_path, aux_out_path, out_path,
                      labels, tmp_folder, target)

    # register virtual cells
    vc_name = 'prospr-6dpf-1-whole-virtual-cells-labels'
    vc_path = os.path.join(input_folder, 'virtual_cells', 'virtual_cells--prosprspaceMEDs.tif')
    inputs = [vc_path]
    outputs = [os.path.join(tmp_folder, 'outputs', vc_name)]
    output_folder = os.path.join(new_folder, 'segmentations')
    registration_impl(inputs, outputs, transformation_file, output_folder,
                      tmp_folder, target, max_jobs, interpolation='nearest',
                      dtype='unsigned short')

    # compute the table for the virtual cells
    vc_table_folder = os.path.join(new_folder, 'tables', vc_name)
    os.makedirs(vc_table_folder, exist_ok=True)
    vc_table = os.path.join(vc_table_folder, 'default.csv')
    if os.path.exists(vc_table):
        assert os.path.islink(vc_table), vc_table
        print("Remove link to previous gene table:", vc_table)
        os.unlink(vc_table)
    vc_path = os.path.join(new_folder, 'segmentations', vc_name + '.h5')
    key = 't00000/s00/0/cells'
    add_max_id(vc_path, key)

    assert os.path.exists(vc_path), vc_path
    resolution = [.55, .55, .55]
    base_attributes(vc_path, key, vc_table, resolution,
                    tmp_folder, target, max_jobs,
                    correct_anchors=False)


# we should encode the source prefix and the transformation file to be used
# in a config file in the transformation folder
def update_registration(transformation_file, input_folder, source_prefix, target, max_jobs,
                        new_tag=None):
    """ Update the prospr segmentation.
    This is a special case of 'update_patch', that applies a new prospr registration.

    Arguments:
        transformation_file [str] - path to the transformation used to register
        input_folder [str] - folder with unregistered data
        source_prefix [str] - prefix of the source data to apply the registration to
        target [str] - target of computation
        max_jobs [int] - max number of jobs for computation
        new_tag [str] - new version tag (default: None)
    """
    prefixes = get_source_names()
    if source_prefix not in prefixes:
        raise ValueError("Invalid source name %s" % source_prefix)

    tag, new_tag = get_tags(new_tag)
    print("Updating platy browser from", tag, "to", new_tag)

    # make new folder structure
    folder = os.path.join('data', tag)
    new_folder = os.path.join('data', new_tag)
    make_folder_structure(new_folder)

    # copy the release folder
    copy_release_folder(folder, new_folder, exclude_prefixes=[source_prefix])

    if source_prefix == "prospr-6dpf-1-whole":
        name_parser = parse_prospr
    else:
        raise NotImplementedError

    # apply new registration to all files of the source prefix
    transformation_file = os.path.abspath(transformation_file)
    apply_registration(input_folder, new_folder,
                       transformation_file, source_prefix,
                       target, max_jobs, name_parser)

    if source_prefix == "prospr-6dpf-1-whole":
        update_prospr(new_folder, input_folder, transformation_file, target, max_jobs)
    else:
        raise NotImplementedError

    add_version(new_tag)
    make_bdv_server_file(new_folder, os.path.join(new_folder, 'misc', 'bdv_server.txt'),
                         relative_paths=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Update prospr registration in platy-browser-data.')
    parser.add_argument('transformation_file', type=str, help="path to transformation file")

    parser.add_argument('--input_folder', type=str, default="data/rawdata/prospr",
                        help="Folder with (not registered) input files")
    help_str = "Prefix for the input data. Please change this if you change the 'input_folder' from its default value"
    parser.add_argument('--source_prefix', type=str, default="prospr-6dpf-1-whole",
                        help=help_str)

    parser.add_argument('--target', type=str, default='slurm',
                        help="Computatin plaform, can be 'slurm' or 'local'")
    parser.add_argument('--max_jobs', type=int, default=100,
                        help="Maximal number of jobs used for computation")

    parser.add_argument('--new_tag', type=str, default='',
                        help="New version tag")

    args = parser.parse_args()
    new_tag = args.new_tag
    new_tag = None if new_tag == '' else new_tag

    update_registration(args.transformation_file, args.input_folder, args.source_prefix,
                        args.target, args.max_jobs, new_tag)
