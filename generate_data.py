import json
import os
from glob import glob
from src.llmtify.representations import create_input


archives_dir = 'test_archives/archives'
output_dir = 'test_archives/inputs'

os.makedirs(output_dir, exist_ok=True)

for archive_file in glob(f'{archives_dir}/*.archive.json'):
    with open(archive_file, 'r') as f:
        archive = json.load(f)
    input_data = create_input(archive['archive'])
    output_file = f"{output_dir}/{archive_file.split('/')[-1].replace('.archive.json', '.input.json')}"
    with open(output_file, 'w') as f:
        json.dump(input_data, f, indent=2)

