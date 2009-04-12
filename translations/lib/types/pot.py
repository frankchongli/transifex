import os, commands, re
from django.conf import settings
from translations.lib.types import (TransManagerMixin, TransManagerError)
from translations.models import POFile, Language
from translations.lib.utils import (run_command, CommandError)

class POTStatsError(Exception):

    def __init__(self, language):
        self.language = language

    def __str__(self):
        return "Could not calculate the statistics using the '%s' " \
               "language." % (self.language)

class FileFilterError(Exception):

    def __str__(self):
        return "The file filter should allows the POTFILES.in file" \
               " for intltool POT-based projects."

class POTManager(TransManagerMixin):
    """ A browser class for POT files. """

    def __init__(self, file_set, path, source_lang, file_filter):
        self.file_set = file_set
        self.path = path
        self.source_lang = source_lang
        self.file_filter = file_filter
        self.msgmerge_path = os.path.join(settings.MSGMERGE_DIR, 
                                     os.path.basename(self.path))

    def get_file_path(self, filename, is_msgmerged=False):
        # All the files should be in the file_set, except the intltool
        # POT file that is created by the system
        if filename in self.file_set or \
           filename.endswith('.pot') and is_msgmerged:
            if is_msgmerged:
                file_path = os.path.join(self.msgmerge_path, filename)
            else:
                file_path = os.path.join(self.path, filename)
        else:
            raise IOError("File not found.")
        return file_path

    def get_file_content(self, filename, is_msgmerged=False):
        file_path = get_file_path(filename, is_msgmerged)
        filef = file(file_path, 'rb')
        file_content = filef.read()
        filef.close()
        return file_content

        
    def get_po_files(self):
        """ Return a list of PO filenames """

        po_files = []
        for filename in self.file_set:
            if filename.endswith('.po'):
                po_files.append(filename)
        po_files.sort()
        return po_files

    def get_langfile(self, lang):
        """ Return a PO filename """

        for filepath in self.get_po_files():
            if self.guess_language(filepath) == lang:
                return filepath

    def guess_language(self, filepath):
        """ Guess a language from a filepath """

        if 'LC_MESSAGES' in filepath:
            fp = filepath.split('LC_MESSAGES')
            return os.path.basename(fp[0][:-1:])
        else:
            return os.path.basename(filepath[:-3:])

    def get_langs(self):
        """ Return all langs tha have a po file for a object """

        langs = []
        for filepath in self.get_po_files():
            langs.append(self.guess_language(filepath))
        langs.sort()
        return langs


    def po_file_stats(self, pofile):
        """ Calculate stats for a POT/PO file """
        error = False
        pofile = os.path.join(self.msgmerge_path, pofile)

        command = "LC_ALL=C LANG=C LANGUAGE=C msgfmt --statistics" \
                  " -o /dev/null %s" % pofile
        (error, output) = commands.getstatusoutput(command)

        if error:
            error = True
    
        r_tr = re.search(r"([0-9]+) translated", output)
        r_un = re.search(r"([0-9]+) untranslated", output)
        r_fz = re.search(r"([0-9]+) fuzzy", output)

        if r_tr: translated = r_tr.group(1)
        else: translated = 0
        if r_un: untranslated = r_un.group(1)
        else: untranslated = 0
        if r_fz: fuzzy = r_fz.group(1)
        else: fuzzy = 0

        return {'translated' : int(translated),
                'fuzzy' : int(fuzzy),
                'untranslated' : int(untranslated),
                'error' : error,}

    def calcule_stats(self, lang, try_to_merge):
        """ 
        Return the statistics of a specificy language for a object after
        merging the file translation.
        """

        filename = self.get_langfile(lang)

        # We might want to skip the msgmerge setting try_to_merge as False
        if try_to_merge:
            (is_msgmerged, file_path) = self.msgmerge(filename,
                                                      self.get_source_file())
        else:
            is_msgmerged=False
            file_path = os.path.join(self.path, filename)

        #Copy the current file (non-msgmerged) to the static dir
        if not is_msgmerged:
            self.copy_file_to_static_dir(filename)

        postats = self.po_file_stats(file_path)

        return {'trans': postats['translated'],
                'fuzzy': postats['fuzzy'],
                'untrans': postats['untranslated'],
                'error': postats['error'],
                'is_msgmerged': is_msgmerged}

    def create_stats(self, lang, object, try_to_merge=True):
        """Set the statistics of a specificy language for a object."""
        try:
            stats = self.calcule_stats(lang, try_to_merge)
            f = self.get_langfile(lang)
            s = POFile.objects.get(object_id=object.id, filename=f)
            if not s.language:
                try:
                    l = Language.objects.by_code_or_alias(code=lang)
                    s.language=l
                except Language.DoesNotExist:
                    pass
        except POTStatsError:
            # TODO: It should probably be raised when a checkout of a 
            # module has a problem. Needs to decide what to do when it
            # happens
            pass
        except POFile.DoesNotExist:
            try:
                l = Language.objects.by_code_or_alias(code=lang)
            except Language.DoesNotExist:
                l = None
            s = POFile.objects.create(language=l, filename=f, 
                                           object=object)
        s.set_stats(trans=stats['trans'], fuzzy=stats['fuzzy'], 
                    untrans=stats['untrans'], error=stats['error'])
        s.is_msgmerged = stats['is_msgmerged']
        return s.save()

    def stats_for_lang_object(self, lang, object):
        """Return statistics for an object in a specific language."""
        try: 
            return POFile.objects.filter(language=lang, 
                                         object_id=object.id)[0]
        except IndexError:
            return None

    def get_stats(self, object):
        """ Return a list of statistics of languages for an object."""
        return POFile.objects.filter(
                   object_id=object.id,
                   is_pot=False,
        ).order_by('-trans_perc')

    def delete_stats_for_object(self, object):
        """ Delete all lang statistics of an object."""
        POFile.objects.filter(object_id=object.id).delete()

    def set_source_stats(self, object, is_msgmerged):
        """Set the source file (pot) in the database"""

        potfile=self.get_source_file()
        if potfile:
            try:
                p=POFile.objects.get(object_id=object.id, is_pot=True)
                p.filename=potfile
                p.is_msgmerged=is_msgmerged
            except POFile.DoesNotExist:
                p = POFile(filename=potfile,
                        is_pot=True,
                        object=object,
                        is_msgmerged=is_msgmerged)

            stats = self.po_file_stats(potfile)
            p.set_stats(trans=stats['translated'], 
                        fuzzy=stats['fuzzy'], 
                        untrans=stats['untranslated'], 
                        error=stats['error'])

            p.save()
        else:
            #TODO: We don't have a source file (POT), what should we do?
            pass

    def get_source_stats(self, object):
        """
        Return the source file (pot) statistics from the database
        """
        try:
            return POFile.objects.get(object_id=object.id, is_pot=True)
        except POFile.DoesNotExist:
            return None

    def get_source_file(self):
        """
        Return the source file (pot) path

        Try to find it in the file_set passed to the PO file instace. 
        If it still fauls, try to find the POT file in the filesystem.
        """
        for filename in self.file_set:
            if filename.endswith('.pot'):
                return filename
        # If there is no POT in the file_set, try to find it in
        # the file system
        filename = self.get_intltool_source_file(self.msgmerge_path)
        return filename

    def get_intltool_source_file(self, po_dir):
        """Return the POT file that might be created by intltool"""
        for root, dirs, files in os.walk(po_dir):
            for filename in files:
                if filename.endswith('.pot'):
                    # Get the relative path
                    rel_path = root.split(os.path.basename(self.path))[1]
                    # Return the relative path of the POT file without 
                    # the / in the start of the POT file path
                    return os.path.join(rel_path, filename)[1:]

    def copy_file_to_static_dir(self, filename):
        """Copy a file to the destination"""
        import shutil

        dest = os.path.join(self.msgmerge_path, filename)

        if not os.path.exists(os.path.dirname(dest)):
            os.makedirs(os.path.dirname(dest))

        shutil.copyfile(os.path.join(self.path, filename), dest)

    def msgmerge(self, pofile, potfile):
        """
        Merge two files and save the output at the settings.MSGMERGE_DIR.
        In case that error, copy the source file (pofile) to the 
        destination without merging.
        """
        is_msgmerged = True
        outpo = os.path.join(self.msgmerge_path, pofile)

        try:
        # TODO: Find a library to avoid call msgmerge by command
            command = "msgmerge -o %(outpo)s %(pofile)s %(potfile)s" % {
                    'outpo' : outpo,
                    'pofile' : os.path.join(self.path, pofile),
                    'potfile' : os.path.join(self.msgmerge_path, potfile),}
            
            (error, output) = commands.getstatusoutput(command)
        except:
            error = True

        if error:
            # TODO: Log this. output var can be used.
            is_msgmerged = False

        return (is_msgmerged, outpo)

    def guess_po_dir(self):
        """ Guess the po/ diretory to run intltool """
        for filename in self.file_set:
            if 'POTFILES.in' in filename:
                if self.file_filter:
                    if re.compile(self.file_filter).match(filename):
                        return os.path.join(self.path, 
                                      os.path.dirname(filename))
        raise FileFilterError

    def intltool_update(self):
        """
        Create a new POT file using "intltool-update -p" from the 
        source files. Return False if it fails.
        """
        po_dir = self.guess_po_dir()
        try:
            command = "cd \"%(dir)s\" && rm -f missing notexist && " \
                      "intltool-update -p" % { "dir" : po_dir, }
            (error, output) = commands.getstatusoutput(command)
        except:
            error = True

        # Copy the potfile if it exist to the merged files directory
        potfile = self.get_intltool_source_file(po_dir)
        if potfile:
            self.copy_file_to_static_dir(potfile)

        if error:
            # TODO: Log this. output var can be used.
            return False

        return True

    def msgfmt_check(self, po_contents):
        """
        Run a `msgfmt -c` on a file (file object).
        Raises a ValueError in case the file has errors.
        """
        try:
            p = run_command('msgfmt -o /dev/null -c -', _input=po_contents)
        except CommandError:
            # TODO: Figure out why gettext is not working here
            raise ValueError, "Your file does not" \
                            " pass by the check for correctness" \
                            " (msgfmt -c). Please run this command" \
                            " on your system to see the errors."
