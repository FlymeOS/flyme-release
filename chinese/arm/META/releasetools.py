#
# Copyright (C) 2015 The Android Open-Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import common
import struct

# The target does not support OTA-flashing
# the partition table, so blacklist it.
DEFAULT_BOOTLOADER_OTA_BLACKLIST = [ 'partition' ]


class BadMagicError(Exception):
  __str__ = "bad magic value"

#
# Huawei Bootloader packed image format
#
# typedef struct meta_header {
#  u32   magic;             /* 0xce1ad63c */
#  u16   major_version;     /* (0x1)-reject images with higher major versions */
#  u16   minor_version;     /* (0x0)-allow images with higer minor versions */
#  char  img_version[64];   /* Top level version for images in this meta */
#  u16   meta_hdr_sz;       /* size of this header */
#  u16   img_hdr_sz;        /* size of img_header_entry list */
# } meta_header_t;

# typedef struct img_header_entry {
#  char   ptn_name[MAX_GPT_NAME_SIZE];
#  u32    start_offset;
#  u32    size;
# } img_header_entry_t


MAGIC = 0xce1ad63c


class HuaweiBootImage(object):

  def __init__(self, data, name=None):
    self.name = name
    self._unpack(data)

  def _unpack(self, data):
    """Unpack the data blob as a Huawei boot image and return the list
    of contained image objects"""
    num_imgs_fmt = struct.Struct("<IHH64sHH")
    header = data[0:num_imgs_fmt.size]
    info = {}
    (
      info["magic"],
      info["major_version"],
      info["minor_version"],
      info["img_version"],
      info["meta_hdr_size"],
      info["img_hdr_size"],
    ) = num_imgs_fmt.unpack(header)

    img_info_format = "<72sLL"
    img_info_size = struct.calcsize(img_info_format)
    num = info["img_hdr_size"] / img_info_size
    size = num_imgs_fmt.size
    imgs = [
         struct.unpack(
             img_info_format,
             data[size + i * img_info_size:size + (i + 1) * img_info_size])
         for i in range(num)
    ]

    if info["magic"] != MAGIC:
      raise BadMagicError

    img_objs = {}
    for name, start, end in imgs:
      if TruncToNull(name):
        img = common.File(TruncToNull(name), data[start:start + end])
        img_objs[img.name] = img

    self.unpacked_images = img_objs

  def GetUnpackedImage(self, name):
    return self.unpacked_images.get(name)


def FindRadio(zipfile):
  try:
    return zipfile.read("RADIO/radio.img")
  except KeyError:
    return None


def FullOTA_InstallEnd(info):
  try:
    bootloader_img = info.input_zip.read("RADIO/bootloader.img")
  except KeyError:
    print "no bootloader.img in target_files; skipping install"
  else:
    WriteBootloader(info, bootloader_img)

  radio_img = FindRadio(info.input_zip)
  if radio_img:
    WriteRadio(info, radio_img)
  else:
    print "no radio.img in target_files; skipping install"


def IncrementalOTA_VerifyEnd(info):
  target_radio_img = FindRadio(info.target_zip)
  source_radio_img = FindRadio(info.source_zip)
  if not target_radio_img or not source_radio_img: return
  target_modem_img = HuaweiBootImage(target_radio_img).GetUnpackedImage("modem")
  if not target_modem_img: return
  source_modem_img = HuaweiBootImage(source_radio_img).GetUnpackedImage("modem")
  if not source_modem_img: return
  if target_modem_img.sha1 != source_modem_img.sha1:
    info.script.CacheFreeSpaceCheck(len(source_modem_img.data))
    radio_type, radio_device = common.GetTypeAndDevice("/modem", info.info_dict)
    info.script.PatchCheck("%s:%s:%d:%s:%d:%s" % (
        radio_type, radio_device,
        len(source_modem_img.data), source_modem_img.sha1,
        len(target_modem_img.data), target_modem_img.sha1))


def IncrementalOTA_InstallEnd(info):
  try:
    target_bootloader_img = info.target_zip.read("RADIO/bootloader.img")
    try:
      source_bootloader_img = info.source_zip.read("RADIO/bootloader.img")
    except KeyError:
      source_bootloader_img = None

    if source_bootloader_img == target_bootloader_img:
      print "bootloader unchanged; skipping"
    elif source_bootloader_img == None:
      print "no bootloader in source target_files; installing complete image"
      WriteBootloader(info, target_bootloader_img)
    else:
      tf = common.File("bootloader.img", target_bootloader_img)
      sf = common.File("bootloader.img", source_bootloader_img)
      WriteIncrementalBootloader(info, tf, sf)
  except KeyError:
    print "no bootloader.img in target target_files; skipping install"

  target_radio_image = FindRadio(info.target_zip)
  if not target_radio_image:
    # failed to read TARGET radio image: don't include any radio in update.
    print "no radio.img in target target_files; skipping install"
  else:
    tf = common.File("radio.img", target_radio_image)

    source_radio_image = FindRadio(info.source_zip)
    if not source_radio_image:
      # failed to read SOURCE radio image: include the whole target
      # radio image.
      print "no radio image in source target_files; installing complete image"
      WriteRadio(info, tf.data)
    else:
      sf = common.File("radio.img", source_radio_image)

      if tf.size == sf.size and tf.sha1 == sf.sha1:
        print "radio image unchanged; skipping"
      else:
        WriteIncrementalRadio(info, tf, sf)


def WriteIncrementalBootloader(info, target_imagefile, source_imagefile):
  try:
    tm = HuaweiBootImage(target_imagefile.data, "bootloader")
  except BadMagicError:
    raise ValueError("bootloader.img bad magic value")
  try:
    sm = HuaweiBootImage(source_imagefile.data, "bootloader")
  except BadMagicError:
    print "source bootloader is not a Huawei boot img; installing complete img."
    return WriteBootloader(info, target_imagefile.data)

  # blacklist any partitions that match the source image
  blacklist = DEFAULT_BOOTLOADER_OTA_BLACKLIST
  for ti in tm.unpacked_images.values():
    if ti not in blacklist:
      si = sm.GetUnpackedImage(ti.name)
      if not si:
        continue
      if ti.size == si.size and ti.sha1 == si.sha1:
        print "target bootloader partition img %s matches source; skipping" % (
            ti.name)
        blacklist.append(ti.name)

  # If there are any images to then write them
  whitelist = [ i.name for i in tm.unpacked_images.values()
                if i.name not in blacklist ]
  if len(whitelist):
    # Install the bootloader, skipping any matching partitions
    WriteBootloader(info, target_imagefile.data, blacklist)


def WriteIncrementalRadio(info, target_imagefile, source_imagefile):
  try:
    target_radio_img = HuaweiBootImage(target_imagefile.data, "radio")
  except BadMagicError:
    print "Magic number mismatch in target radio image"
    raise ValueError("radio.img bad magic value")

  try:
    source_radio_img = HuaweiBootImage(source_imagefile.data, "radio")
  except BadMagicError:
    print "Magic number mismatch in source radio image"
    source_radio_img = None

  write_full_modem = True
  if source_radio_img:
    target_modem_img = target_radio_img.GetUnpackedImage("modem")
    if target_modem_img:
      source_modem_img = source_radio_img.GetUnpackedImage("modem")
      if source_modem_img:
        WriteIncrementalModemPartition(info, target_modem_img, source_modem_img)
        write_full_modem = False

  # Write the full images, skipping modem if so directed.
  #
  # NOTE: Some target flex radio images are zero-filled, and must
  #       be flashed to trigger the flex update "magic".  Do not
  #       skip installing target partition images that are identical
  #       to its corresponding source partition image.
  blacklist = []
  if not write_full_modem:
    blacklist.append("modem")
  WriteHuaweiBootPartitionImages(info, target_radio_img, blacklist)


def WriteIncrementalModemPartition(info, target_modem_image,
                                   source_modem_image):
  tf = target_modem_image
  sf = source_modem_image
  pad_tf = False
  pad_sf = False
  blocksize = 4096

  partial_tf = len(tf.data) % blocksize
  partial_sf = len(sf.data) % blocksize

  if partial_tf:
     pad_tf = True
  if partial_sf:
     pad_sf = True
  b = common.BlockDifference("modem", common.DataImage(tf.data,False, pad_tf),
                             common.DataImage(sf.data,False, pad_sf))
  b.WriteScript(info.script, info.output_zip)


def WriteRadio(info, radio_img):
  info.script.Print("Writing radio...")

  try:
    huawei_boot_image = HuaweiBootImage(radio_img, "radio")
  except BadMagicError:
    raise ValueError("radio.img bad magic value")

  WriteHuaweiBootPartitionImages(info, huawei_boot_image)


def WriteHuaweiBootPartitionImages(info, huawei_boot_image, blacklist=[]):
  WriteGroupedImages(info, huawei_boot_image.name,
                     huawei_boot_image.unpacked_images.values(), blacklist)


def WriteGroupedImages(info, group_name, images, blacklist=[]):
  """Write a group of partition images to the OTA package,
  and add the corresponding flash instructions to the recovery
  script.  Skip any images that do not have a corresponding
  entry in recovery.fstab."""
  for i in images:
    if i.name not in blacklist:
      WritePartitionImage(info, i, group_name)


def WritePartitionImage(info, image, group_name=None):
  filename = "%s.img" % image.name
  if group_name:
    filename = "%s.%s" % (group_name, filename)

  try:
    info.script.Print("writing partition image %s" % image.name)
    _, device = common.GetTypeAndDevice("/" + image.name, info.info_dict)
  except KeyError:
    print "skipping flash of %s; not in recovery.fstab" % image.name
    return

  common.ZipWriteStr(info.output_zip, filename, image.data)

  info.script.AppendExtra('package_extract_file("%s", "%s");' %
                          (filename, device))


def WriteBootloader(info, bootloader,
                    blacklist=DEFAULT_BOOTLOADER_OTA_BLACKLIST):
  info.script.Print("Writing bootloader...")
  try:
    huawei_boot_image = HuaweiBootImage(bootloader,"bootloader")
  except BadMagicError:
    raise ValueError("bootloader.img bad magic value")

  common.ZipWriteStr(info.output_zip, "bootloader-flag.txt",
                     "updating-bootloader" + "\0" * 13)
  common.ZipWriteStr(info.output_zip, "bootloader-flag-clear.txt", "\0" * 32)

  _, misc_device = common.GetTypeAndDevice("/misc", info.info_dict)

  info.script.AppendExtra(
      'package_extract_file("bootloader-flag.txt", "%s");' % misc_device)

  # OTA does not support partition changes, so
  # do not bundle the partition image in the OTA package.
  WriteHuaweiBootPartitionImages(info, huawei_boot_image, blacklist)

  info.script.AppendExtra(
      'package_extract_file("bootloader-flag-clear.txt", "%s");' % misc_device)


def TruncToNull(s):
  if '\0' in s:
    return s[:s.index('\0')]
  else:
    return s
