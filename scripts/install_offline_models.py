"""Install available offline Argos models at image-build time; no runtime API calls."""
import argostranslate.package

argostranslate.package.update_package_index()
available = argostranslate.package.get_available_packages()
for source, target in (("en", "hi"), ("hi", "en")):
    matches = [p for p in available if p.from_code == source and p.to_code == target]
    if matches:
        package_path = matches[0].download()
        argostranslate.package.install_from_path(package_path)
