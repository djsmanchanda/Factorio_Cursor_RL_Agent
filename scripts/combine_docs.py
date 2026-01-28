import os

def combine_markdown_files(output_file, docs_dir='docs', readme_file='README.md'):
    combined_content = []
    
    # 1. Add README.md first
    if os.path.exists(readme_file):
        with open(readme_file, 'r', encoding='utf-8') as f:
            combined_content.append(f"--- FILE: {readme_file} ---\n\n" + f.read())
    
    # 2. Add all .md files in docs/ sorted by filename
    if os.path.exists(docs_dir):
        files = sorted([f for f in os.listdir(docs_dir) if f.endswith('.md')])
        for filename in files:
            file_path = os.path.join(docs_dir, filename)
            with open(file_path, 'r', encoding='utf-8') as f:
                combined_content.append(f"\n\n--- FILE: {docs_dir}/{filename} ---\n\n" + f.read())
    
    # 3. Write to output file
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("\n".join(combined_content))
    print(f"Successfully combined {len(combined_content)} files into {output_file}")

if __name__ == "__main__":
    combine_markdown_files('FULL_DOCUMENTATION.md')
