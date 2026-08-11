# LibreOffice converter Lambda. Built from apps/rmf-migrator/backend:
#
#   docker build -f converter.Dockerfile -t rmf-converter .
#
# Not the public.ecr.aws/lambda/python base the other functions would use:
# Amazon Linux 2023 ships no LibreOffice package in any enabled repo
# (`dnf install libreoffice-writer` -> "No package matches"), and the only
# alternatives are unpinned third-party RPMs or a hand-rolled tarball. Debian
# carries it on the security-updates track, so the base is Debian plus the AWS
# Lambda Runtime Interface Client, which is the documented custom-base path.
#
# Pinned by digest so the image cannot drift — see the supply-chain rule in the
# repo's security posture. The tag is left in the comment because a bare digest
# says nothing about what it is: python:3.12-slim-bookworm.
FROM python@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

ENV LAMBDA_TASK_ROOT=/var/task

# soffice needs a writable HOME for its per-user profile; /tmp is the only
# writable path in a Lambda container, and it is writable by any uid there.
# Set before the version probe below, which starts soffice.
ENV HOME=/tmp

# -nogui: the Writer filters without the X11/GTK stack. Smaller image, and the
# code that would drive a display is not installed at all. No Calc, Impress or
# Java either — Writer alone means the BIFF and PowerPoint import filters are
# not present at all, which is why the pinned --infilter has a second line of
# defense behind it rather than being the only thing standing between a forged
# container and a memory-unsafe parser.
#
# Deliberately not version-pinned, unlike the base image and the wheels: this
# is the CVE-bearing component, its Debian security track is where the fixes
# arrive, and a pinned version silently ages out of the mirror. Patch-level
# movement within the series is the point; a series change is not, and the
# assertion below is what makes the difference visible.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Deny the converter the network from inside LibreOffice. A .doc can name a
# URL for a linked graphic, and this build fetches it while converting — a
# callback out of the CUI boundary from a document someone was mailed. Being a
# Lambda does not prevent it: a function outside a VPC has full egress.
#
# Two parts, because one is not enough. The policy file blocks the content
# fetch, but soffice still probed the URL with OPTIONS and HEAD; removing the
# UCB providers that speak HTTP and FTP leaves nothing to probe with. With both,
# the beacon listener recorded no requests at all, and a real .doc still
# converted with its text and headings intact — the runs are in the plan's
# Gate 1 section. The network half (a VPC with no NAT or internet gateway, S3
# and KMS endpoints only) is Task 12's, and neither substitutes for the other.
#
# libucpchelp1 (bundled help) and libucppkg1 (extension packages) are local and
# stay. Deleting these two is what a rebuild would silently undo, so the probe
# belongs with the Gate 1 evidence rather than in anyone's memory.
#
# No `-f`, and the removal is bracketed by assertions: `rm -f` exits 0 when the
# path is absent, so a base-image or package-layout move that renames these
# files would leave both providers installed, the build green, and every
# artifact here still claiming the control holds. Measured — building this file
# with the two names changed produces an image with `libucpdav1.so` and
# `libucpftp1.so` present and `docker build` reporting success.
COPY converter-no-remote-links.xcd /usr/lib/libreoffice/share/registry/no-remote-links.xcd
RUN test -e /usr/lib/libreoffice/program/libucpdav1.so \
    && test -e /usr/lib/libreoffice/program/libucpftp1.so \
    && rm /usr/lib/libreoffice/program/libucpdav1.so \
          /usr/lib/libreoffice/program/libucpftp1.so \
    && ! ls /usr/lib/libreoffice/program | grep -qE '^libucp(dav|ftp)'

# Record the build and refuse to produce an image outside the series the
# containment evidence covers. Gate 1 in
# docs/superpowers/plans/2026-08-08-doc-conversion.md establishes that
# `--infilter=MS Word 97` actually binds the import filter — measured on
# LibreOffice 7.4.7.2, and --infilter handling is build-specific. Without this
# grep, apt moving to another series would silently rebuild an image whose
# refusal behaviour nothing has checked, from an unchanged commit, while every
# artifact in the repo still asserts the control holds. Bumping the expected
# series means re-running Gate 1's Step 4b first and recording the result.
#
# The Gate 3 controls assert themselves here too, because both of their
# mechanisms fail quietly. COPY creates whatever destination it is given, so a
# registry directory that moves leaves the policy file somewhere configmgr
# never reads; hence `test -f` on the path that has to be right.
#
# The version probe was once described as validating the policy file's
# contents. Measured against this build, it does not: configmgr throws only on
# malformed XML or a wrong `oor:type` on a real property (soffice aborts, rc
# 134, no version printed, build fails). An unknown property name and a
# misspelled node name are both discarded in silence — `soffice --version`
# exits 0, prints the version, and a real .doc still converts, so the two
# likeliest maintainer typos would ship an image with the egress policy
# quietly dropped. The name loop is what catches those: every `oor:name` the
# policy file uses — the component, the two nodes, and the three properties —
# must appear in the schema the build ships. Read out of the .xcd rather than
# restated here, so adding a property to it cannot leave the property
# unasserted. Absent from main.xcd means the spelling in the .xcd is not the
# one this build knows, whatever the reason.
#
# The rm is not tidiness: this probe runs before USER, so it leaves a
# root-owned profile under HOME=/tmp, and soffice started by uid 10001 then
# blocks forever trying to write it (dconf reports "Permission denied" and the
# process never exits). Leaving it turns every conversion into a timeout.
RUN REGISTRY=/usr/lib/libreoffice/share/registry \
    && mkdir -p ${LAMBDA_TASK_ROOT} \
    && test -f $REGISTRY/no-remote-links.xcd \
    && test ! -e /usr/lib/libreoffice/program/libucpdav1.so \
    && test ! -e /usr/lib/libreoffice/program/libucpftp1.so \
    && NAMES=$(grep -oE 'oor:name="[^"]+"' $REGISTRY/no-remote-links.xcd | cut -d'"' -f2) \
    && test -n "$NAMES" \
    && for name in $NAMES; do grep -q "$name" $REGISTRY/main.xcd || exit 1; done \
    && /usr/bin/soffice --version | tee ${LAMBDA_TASK_ROOT}/LIBREOFFICE_VERSION \
    && grep -q 'LibreOffice 7\.4\.' ${LAMBDA_TASK_ROOT}/LIBREOFFICE_VERSION \
    && rm -rf /tmp/.config /tmp/.cache /tmp/.dbus

# boto3/botocore are not provided by this base the way they are by the AWS one,
# so they are installed at the versions requirements-lock.txt pins for the
# other functions.
RUN pip install --no-cache-dir awslambdaric==4.0.2 boto3==1.43.42 botocore==1.43.42

COPY src/rmf_migrator ${LAMBDA_TASK_ROOT}/rmf_migrator
# Build hosts leave their own __pycache__ in src/; those .pyc are compiled by
# whatever interpreter ran the tests, not this one.
RUN find ${LAMBDA_TASK_ROOT}/rmf_migrator -name __pycache__ -type d -exec rm -rf {} +

WORKDIR ${LAMBDA_TASK_ROOT}

# The one container in this system that feeds attacker-controlled bytes to a
# memory-unsafe C++ parser, so it does not run as root. Code execution inside
# soffice.bin dies with the process; the only way to outlive it is writing to
# /var/task, whose .py files the next invocation in a warm sandbox executes
# holding this role's S3 and KMS access. Root-owned and not group- or
# world-writable (COPY's default), plus a non-root uid, closes that route.
# HOME is /tmp, which any uid can write in the Lambda sandbox.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin converter
USER 10001

# A custom base carries no Lambda bootstrap of its own, so the runtime client
# is the entrypoint. aws_lambda_function.converter (Task 12) must therefore
# leave `image_config` unset — an override there replaces this pair, and an
# override that drops the RIC produces a function that never starts.
ENTRYPOINT ["/usr/local/bin/python", "-m", "awslambdaric"]
CMD ["rmf_migrator.converter_lambda.handler.handler"]
