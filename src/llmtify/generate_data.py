import json
import os
from glob import glob
from src.llmtify.representations import create_input
from src.llmtify.nomad_api import fetch_by_entry_ids


def process_archive_files(archives_dir='test_archives/archives', output_dir='test_archives/inputs'):
    os.makedirs(output_dir, exist_ok=True)

    for archive_file in glob(f'{archives_dir}/*.archive.json'):
        with open(archive_file, 'r') as f:
            archive = json.load(f)
        input_data = create_input(archive)
        output_file = f"{output_dir}/{archive_file.split('/')[-1].replace('.archive.json', '.input.json')}"
        with open(output_file, 'w') as f:
            json.dump(input_data, f, indent=2)

def process_archives(archives):
    """Process a list of archive dictionaries and generate input files."""
    inputs = []
    for archive in archives:
        input_data = create_input(archive)
        inputs.append(input_data)
    return inputs

def get_input_for_id(entry_id, save_dir=None):
    """Fetch the archive for a given entry_id and generate input data."""
    archives = fetch_by_entry_ids([entry_id], save_dir=save_dir)
    if entry_id in archives:
        return create_input(archives[entry_id])
    else:
        print(f"Warning: No archive found for entry_id {entry_id}.")
        return None
