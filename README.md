# The Grand IPA Archive

Forked from relikd, indexed ~40k more apps. 

NO APPS ARE HOSTED ON THIS SITE. 

Hello, this project aims to provide a searchable and filterable index for .ipa files.
None of the linked files are mine, nor am I involved in any capacity on the referenced projects.
I merely forked the crawler to index IPA files in various [Archive.org](https://archive.org) collections.
The list of indexed collections can be found at [data/urls.json](data/urls.json).


## Using the webpage

You can add the [IPA Archive](https://stuffed18.github.io/ipa-archive-updated/) webpage to your homescreen.
Note however, that each time you click on the app icon, it will reload the whole database and clear your previous results.
To prevent that, use Safari instead.
Switching back to the already open webpage, will not trigger a reload.
The homescreen icon is still useful as bookmark though ;-)

Additionally, your configuration is saved in the URL.
For example, if you have an iPad 1. Gen, you can select device "iPad" and maxOS "5.1.1".
Then click on search and safe that URL to your homescreen.
(Or wait until you have configured your Plist server and save that URL instead)


## Starting Plist Server

The Plist server needs either Python or PHP.

You must start the service on a network location that is accessible to your iDevice.
That can be, for example, your local Mac/PC which is accessible through your home network (LAN).
You may need to determine the IP address of your PC.


### ... with Python

With python, the IP address *should* be determined automatically.
Download [tools/plist_server.py](tools/plist_server.py) and start the server:

```sh
python3 tools/plist_server.py
```

it will print out something like `Server started http://192.168.0.1:8026`.
Use this address on the IPA Archive webpage.
If the IP starts with `127.x.x.x` or `10.x.x.x`, you will need to find the IP address manually and use that instead.


### ... with PHP

Similar to python, you can download [tools/plist_server/index.php](tools/plist_server/index.php) and start the server with:

```sh
php -S 0.0.0.0:8026 -t tools/plist_server
```

If you are already inside the `plist_server` directory, you can omit the `-t` flag.
Note, we use `0.0.0.0` instead of localhost, to make the server available to other network devices.
However, for the IPA Archive webpage you should use your own IP address, e.g., `http://192.168.0.1:8026`.


### Local IP address

If the Python script does not detect the IP correctly - or you use PHP - you have to find the IP address manually.
On a Mac you can run `ipconfig getifaddr en0`.
Similar commands exist on Linux and Windows.


## Development

### Requirements

- `main.py` has a dependency on [RemoteZip](https://github.com/gtsystem/python-remotezip) (`pip install remotezip`)
- `image_optim.sh` uses [ImageOptim](https://github.com/ImageOptim/ImageOptim) (requires a Mac)
- `convert_plist.sh` uses PlistBuddy (probably requires a Mac)


### Database schema

The column `done` is encoded as follows:
- `0` (queued, needs processing)
- `1` (done)
- `3` (error, maybe fixable, needs attention)
- `4` (error, unfixable, ignore in export)


### General workflow

To add files to the archive follow these steps:

1. `python3 main.py add URL`
2. `python3 main.py run`
3. If any of the URLs failed, check if it can be fixed. (though most likely the ipa-zip file is broken)
    - If fixable, `python3 main.py err reset` # set all err to done=0 and print errors again
    - If unfixable, `python3 main.py set err ID1 ID2` # mark ids done=4
4. `./tools/image_optim.sh` (this will convert all .png files to .jpg)
5. `python3 main.py export json`


To update:
- `python3 main.py update` # check all links (if not udpated recently)
- `python3 main.py update [url|base_url_id]`  # force update
- Then run the same steps as after adding an url


Userful helper:
- `./tools/check_error_no_plist.sh` # checks that no plist exists for a done=4 entry
- `./tools/check_missing_img.sh` # checks that for each .plist an .jpg exists
- `./tools/convert_plist.sh 21968` # convert json-like format to XML
- `./main.py get url 21968` # print URL of entry
- `./main.py get img 21968` # force (re)download of .png image
- `./main.py get ipa 21968` # download ipa file for debugging
