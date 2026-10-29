"""Distribution integrity and safe restore; no scientific experiment fixtures."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

_spec = importlib.util.spec_from_file_location('package_step1_final', Path(__file__).parents[1] / 'scripts/package_step1_final.py')
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


def test_member_content_tamper_is_rejected_even_when_filename_matches(tmp_path):
    source = tmp_path / 'source'; source.write_bytes(b'changed')
    archive = tmp_path / 'sample.tar.gz'
    module.write_archive(archive, {'runs/example.json': source})
    with pytest.raises(ValueError, match='hash mismatch'):
        module.verify_archive(archive, {'runs/example.json': hashlib.sha256(b'original').hexdigest()})


def test_restore_rejects_parent_escape_without_creating_outside_file(tmp_path):
    archive = tmp_path / 'unsafe.tar.gz'
    with tarfile.open(archive, 'w:gz') as stream:
        member = tarfile.TarInfo('../outside.txt'); member.size = 1
        stream.addfile(member, io.BytesIO(b'x'))
    with pytest.raises(ValueError, match='Unsafe'):
        module.verify_archive(archive, {'../outside.txt': hashlib.sha256(b'x').hexdigest()}, tmp_path / 'restore')
    assert not (tmp_path / 'outside.txt').exists()


def test_restore_never_overwrites_existing_destination(tmp_path):
    source = tmp_path / 'source'; source.write_bytes(b'original')
    archive = tmp_path / 'safe.tar.gz'
    module.write_archive(archive, {'runs/example.json': source})
    dest = tmp_path / 'existing'; dest.mkdir(); (dest / 'keep').write_bytes(b'keep')
    with pytest.raises(FileExistsError):
        module.verify_archive(archive, {'runs/example.json': module.digest(source)}, dest)
    assert (dest / 'keep').read_bytes() == b'keep'


def test_public_metadata_can_be_verified_and_restored_without_trajectory_files(tmp_path):
    source = tmp_path / 'source'; source.write_bytes(b'{"trajectory.npz":"not bundled"}')
    archive = tmp_path / 'public.tar.gz'
    module.write_archive(archive, {'runs/example/receipt.json': source})
    expected = {'runs/example/receipt.json': module.digest(source)}
    assert module.verify_archive(archive, expected) == 1
    assert module.verify_archive(archive, expected, tmp_path / 'restored') == 1
    assert not (tmp_path / 'restored/runs/example/trajectory.npz').exists()


def test_full_local_verification_rejects_missing_trajectory_even_with_valid_metadata(tmp_path, monkeypatch):
    receipt = tmp_path / module.STUDY / 'results/receipt.json'
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({'trajectory.npz': hashlib.sha256(b'original trajectory').hexdigest()}))
    expected = {receipt.relative_to(tmp_path).as_posix(): module.digest(receipt)}
    package = tmp_path / module.PACKAGE
    archive = package / module.ARCHIVE
    info = module.write_archive(archive, {next(iter(expected)): receipt})
    module.write_new(package / 'publication_manifest.json', {
        'published_files': {}, 'extra_archive_files': {},
        'archive': {'path': module.ARCHIVE, **info}})
    monkeypatch.setattr(module, 'indexed_expected', lambda _: expected)
    assert module.verify(tmp_path)['full_local_data_verified'] is False
    with pytest.raises(ValueError, match='trajectory.npz'):
        module.verify(tmp_path, full=True)


def test_wrong_raw_archive_hash_rejected_before_destination_creation(tmp_path):
    package = tmp_path / module.PACKAGE
    module.write_new(package / 'publication_manifest.json', {'raw_archive': {'sha256': '0' * 64}})
    archive = tmp_path / 'wrong.tar.gz'; archive.write_bytes(b'wrong bytes')
    with pytest.raises(ValueError, match='SHA-256 differs'):
        module.restore_raw(tmp_path, archive, tmp_path / 'new_destination')
    assert not (tmp_path / 'new_destination').exists()
