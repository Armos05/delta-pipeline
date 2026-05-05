This is how to run the apptainer:

apptainer run --bind /home/akmisr@d.uzh.ch/scripts/DELTA_scripts/analysis_pipeline/src:/opt/delta/src  /mnt/data/docker/custom/delta.sif convert --raw /mnt/data/docker/custom/ --bids /mnt/data/docker/custom/bids_output --config /home/akmisr@d.uzh.ch/scripts/DELTA_scripts/analysis_pipeline/configs/dcm2bids_config.json --subjects sub-01 --state /mnt/data/docker/custom/delta_state.json --force

for mnt nfs have to bind again:
 apptainer run --bind /home/akmisr@d.uzh.ch/scripts/DELTA_scripts/analysis_pipeline/src:/opt/delta/s
rc --bind /mnt/nfs/stricon_data:/mnt/nfs/stricon_data  /mnt/data/docker/custom/delta.sif convert --raw /mnt/nfs/stricon_data/DELTA_files/DELTA_raw_data/ --bids /mnt/nfs/stricon_data/DELTA_files/DELTA_raw_data/bids_output --config /home/akmisr@d.uzh.ch/scripts/DELTA_scripts/analysis_pipeline/configs/dcm2bids_config.json --subjects sub-03 --state /mnt/nfs/stricon_data/DELTA_files/DELTA_raw_data/delta_state.json