import argparse
from datetime import date
import megabump_utils as mb

def start_integerate(args):
    current_branch = mb.git_current_branch(repo_dir=mb.iree_path)
    print(f"Current branch: {current_branch}")
    if current_branch != "main":
        mb.git_exec(["switch", "main"], repo_dir=mb.iree_path)
    mb.git_fetch(repo_dir=mb.iree_path)
    mb.git_exec(["pull", "--ff-only", "origin", "main"], repo_dir=mb.iree_path)
    mb.git_exec(["submodule", "update", "--init"], repo_dir=mb.iree_path)
    base_branch_name = f"integrate-llvm-{date.today().strftime('%Y%m%d')}"
    branch_name = base_branch_name
    counter = 1
    while mb.git_branch_exists(branch_name, repo_dir=mb.iree_path):
        branch_name = f"{base_branch_name}_{counter}"
        counter += 1
    print(f"Creating branch {branch_name}")
    mb.git_create_branch(branch_name, ref="origin/main", repo_dir=mb.iree_path)
    mb.git_exec(["commit", "--allow-empty", "-m", f"Start LLVM integrate {branch_name}"], repo_dir=mb.iree_path)
    return branch_name

def parse_arguments(argv):
    parser = argparse.ArgumentParser(description="Start IREE integrate")
    return parser.parse_args(argv)
