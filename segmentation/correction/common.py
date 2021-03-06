# import os

BOUNDARY_PATH = '/g/arendt/EM_6dpf_segmentation/corrections_and_proofreading/data.n5'
BOUNDARY_KEY = 'boundaries'

PAINTERA_PATH = '/g/kreshuk/data/arendt/platyneris_v1/data.n5'
PAINTERA_KEY = 'volumes/paintera/proofread_cells_multiset'

RAW_PATH = '/g/arendt/EM_6dpf_segmentation/platy-browser-data/data/rawdata/sbem-6dpf-1-whole-raw.n5'
RAW_KEY = 'setup0/timepoint0'

TMP_PATH = './data.n5'
ROI_PATH = './configs/rois.json'
LABEL_MAPPING_PATH = './configs/label_mapping.json'

# NOTE we use the segmentation that was already partially proof-read in an initial set-up
# SEG_PATH = os.path.join('/g/arendt/EM_6dpf_segmentation/platy-browser-data/data/1.0.1/images',
#                         'local/sbem-6dpf-1-whole-segmented-cells.n5')
# SEG_KEY = 'setup0/timepoint0'

SEG_PATH = TMP_PATH
SEG_KEY = 'volumes/segmentation2'
