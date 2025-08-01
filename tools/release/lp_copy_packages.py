#!/usr/bin/env python3
# This file is part of Checkbox.
#
# Copyright 2023-2025 Canonical Ltd.
# Written by:
#   Sylvain Pineau <sylvain.pineau@canonical.com>
#   Massimiliano Girardi <massimiliano.girardi@canonical.com>
#
# Checkbox is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3,
# as published by the Free Software Foundation.
#
# Checkbox is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Checkbox.  If not, see <http://www.gnu.org/licenses/>.
"""
This script facilitates the copying of packages from one Launchpad
Personal Package Archive (PPA) to another. It is designed for copying
every Checkbox package from a source PPA to a destination PPA
without the need for rebuilding.

Note: This script uses the LP_CREDENTIALS environment variable
"""
import sys
import lazr
import argparse

from utils import get_launchpad_client


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_owner", help="Name of source the ppa owner")
    parser.add_argument("source_ppa", help="Source ppa to copy from")
    parser.add_argument("dest_owner", help="Name of destination the ppa owner")
    parser.add_argument("dest_ppa", help="Destination ppa to copy to")

    return parser.parse_args(argv)


def get_ppa(lp, ppa_name: str, ppa_owner: str):
    ppa_owner = lp.people[ppa_owner]
    return ppa_owner.getPPAByName(name=ppa_name)


def copy_packages(source_owner, source_ppa, dest_owner, dest_ppa):
    """
    Copy all packages from a source PPA to a destination PPA without
    rebuilding.
    """
    lp = get_launchpad_client()

    source_ppa = get_ppa(lp, source_ppa, source_owner)
    dest_ppa = get_ppa(lp, dest_ppa, dest_owner)

    packages = source_ppa.getPublishedSources(
        order_by_date=True, status="Published"
    )

    # Copy each package from the source PPA to the destination PPA,
    # without rebuilding them
    for package in packages:
        try:
            dest_ppa.copyPackage(
                from_archive=source_ppa,
                include_binaries=True,
                to_pocket=package.pocket,
                source_name=package.source_package_name,
                version=package.source_package_version,
            )
            print(
                f"Copied {package.source_package_name} "
                f"version {package.source_package_version} "
                f"from {source_ppa} to {dest_ppa} "
                "(without rebuilding)"
            )
        except lazr.restfulclient.errors.BadRequest as e:
            # This is expected when trying to copy a package to a target distro
            # that is EOL and can be safely ignored
            if "is obsolete and will not accept new uploads" not in str(e):
                raise
            print(
                f"Skipped {package.source_package_name} "
                f"version {package.source_package_version} "
                "(target series is obsolete)"
            )


def main(argv):
    args = parse_args(argv)
    copy_packages(
        args.source_owner,
        args.source_ppa,
        args.dest_owner,
        args.dest_ppa,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
