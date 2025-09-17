# ARM TF-A for Android AVF / Trusty TEE

The Trusty team has worked closely with ARM over the past few years to refine
the EL3 SPMC component and Hafnium for an optimized integration with
Android AVF and Trusty TEE.

This document describes the current additional CLs required on top of the
latest TF-A and Hafnium releases in order to properly support Android AVF and Trusty TEE.

## EL3 SPMC

Outstanding CLs from Google being reviewed by ARM can be found at:

[https://review.trustedfirmware.org/q/owner:ahomescu@xwf.google.com+status:open](https://review.trustedfirmware.org/q/owner:ahomescu@xwf.google.com+status:open)

The best method to apply the Google specific patches from the latest TF-A v2.13 release
is to generate patches, following below approach.

- use an android checkout (FSK for example), create a local `head` branch:

    ```
    cd external/trusty/arm-trusted-firmware
    repo start head
    ```

- add tfa as a git remote and fetch the v2.13 tag:

    ```
    git remote add tfa https://github.com/ARM-software/arm-trusted-firmware.git
    git fetch tfa --tags
    ```

- use the commit hash for the `refs/tags/v2.13` entry to create a local branch:

    ```
    git co -b tfa-v2.13 v2.13
    ```

- generate the patches from the v2.13 branch to head:

    verify that you have two local branches, head and tfa-v2.13 with:

    ```
    git branch -vv
    ```

    generate the patches:

    ```
    git format-patch tfa-v2.13..head -- services/std_svc/spm/el3_spmc
    ```

The el3 spmc manifest declaring Trusty as the SP, can be found at `trusty/device/arm/generic-arm64/project/el3spmc/tos_fw_config.dts`

## Hafnium


The best method to apply the Google specific patches from the latest Hafnium v2.13 release
is to generate patches, following below approach.

- use an android checkout (FSK for example), create a local `head` branch:

    ```
    cd external/hafnium
    repo start head
    ```

- add hafnium as a git remote and fetch the v2.13 tag:

    ```
    git remote add hafnium https://github.com/TF-Hafnium/hafnium
    git fetch hafnium --tags
    ```

- use the commit hash for the `refs/tags/v2.13` entry to create a local branch:

    ```
    git co -b hafnium-v2.13 v2.13
    ```

- generate the patches from the v2.13 branch to head:

    verify that you have two local branches, head and tfa-v2.13 with:

    ```
    git branch -vv
    ```

    generate the patches:

    ```
    git format-patch hafnium-v2.13..head
    ```

The hafnium manifest declaring Trusty as SP can be found at `trusty/device/arm/generic-arm64/project/hafnium`