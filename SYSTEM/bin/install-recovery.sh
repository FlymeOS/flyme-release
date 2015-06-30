#!/system/bin/sh
if ! applypatch -c EMMC:/dev/block/platform/msm_sdcc.1/by-name/recovery:9781248:0676653ea3daf33cac1c800940bbe2c539197aa4; then
  applypatch -b /system/etc/recovery-resource.dat EMMC:/dev/block/platform/msm_sdcc.1/by-name/boot:9066496:7c0330600507a4d885be7b7d60cfa4cc6ad4a56d EMMC:/dev/block/platform/msm_sdcc.1/by-name/recovery 0676653ea3daf33cac1c800940bbe2c539197aa4 9781248 7c0330600507a4d885be7b7d60cfa4cc6ad4a56d:/system/recovery-from-boot.p && log -t recovery "Installing new recovery image: succeeded" || log -t recovery "Installing new recovery image: failed"
else
  log -t recovery "Recovery image already installed"
fi
