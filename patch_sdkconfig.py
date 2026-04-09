lines = []
with open("sdkconfig", "r") as f:
    lines = f.readlines()

# Remove old partition config if exists
lines = [l for l in lines if not l.startswith("CONFIG_PARTITION_TABLE")]

lines.append("\nCONFIG_PARTITION_TABLE_CUSTOM=y\n")
lines.append("CONFIG_PARTITION_TABLE_CUSTOM_FILENAME=\"partitions.csv\"\n")
lines.append("CONFIG_PARTITION_TABLE_FILENAME=\"partitions.csv\"\n")
lines.append("CONFIG_PARTITION_TABLE_OFFSET=0x8000\n")
lines.append("CONFIG_PARTITION_TABLE_MD5=y\n")

with open("sdkconfig", "w") as f:
    f.writelines(lines)
